#include "ir/types.hh"

#include <algorithm>
#include <map>
#include <memory>
#include <mutex>
#include <numeric>
#include <tuple>
#include <unordered_map>
#include <unordered_set>


namespace pl4g::ir {

  namespace {

    // Every type made, kept for the life of the process, and the lock that
    // guards making them: a language server checks on more than one thread.
    struct Store {
      std::recursive_mutex lock;
      // Every type made, kept for the life of the process.
      std::vector<std::unique_ptr<Type>> kept;
      std::map<std::pair<std::vector<std::pair<std::string, std::int64_t>>, Rational>, std::unique_ptr<Unit>> units;
      std::map<std::tuple<unsigned, bool, const Unit*>, std::unique_ptr<IntType>> ints;
      std::map<std::pair<unsigned, const Unit*>, std::unique_ptr<FloatType>> floats;
      // The types built of others, by their structure: what pypl4g's
      // TypeContext asks its dictionaries, which compare by structure too.
      std::unordered_multimap<std::size_t, const Type*> interned;
      // The pointers to an element `parts_of` answers with, by the element:
      // pypl4g keeps these apart from the context's, as objects of their own.
      std::unordered_multimap<std::size_t, const PtrType*> element_pointers;
      std::map<std::string, ProductType*> named;
    };

    Store& store()
    {
      static Store* the = new Store;
      return *the;
    }

    __int128 gcd128(__int128 a, __int128 b)
    {
      if (a < 0)
        a = -a;
      if (b < 0)
        b = -b;
      while (b != 0)
        a = std::exchange(b, a % b);
      return a;
    }

    std::string decimal(__int128 v)
    {
      if (v == 0)
        return "0";
      bool negative = v < 0;
      unsigned __int128 m = negative ? -(unsigned __int128)(v) : (unsigned __int128)(v);
      std::string out;
      for (; m > 0; m /= 10)
        out.insert(out.begin(), char('0' + int(m % 10)));
      return negative ? "-" + out : out;
    }

    // `value` in raised digits.
    std::string superscript(std::int64_t value)
    {
      static constexpr std::string_view digits[] = {
        "\N{SUPERSCRIPT ZERO}", "\N{SUPERSCRIPT ONE}", "\N{SUPERSCRIPT TWO}", "\N{SUPERSCRIPT THREE}",
        "\N{SUPERSCRIPT FOUR}", "\N{SUPERSCRIPT FIVE}", "\N{SUPERSCRIPT SIX}", "\N{SUPERSCRIPT SEVEN}",
        "\N{SUPERSCRIPT EIGHT}", "\N{SUPERSCRIPT NINE}",
      };
      std::string out;
      for (char c : std::to_string(value))
        out += c == '-' ? std::string("-") : std::string(digits[c - '0']);
      return out;
    }

    // Every base unit written out, with its exponent where it has one.
    std::vector<std::string> each(const std::vector<std::pair<std::string, std::int64_t>>& powers)
    {
      std::vector<std::string> out;
      for (const auto& [n, e] : powers)
        out.push_back(e == 1 ? n : n + superscript(e));
      return out;
    }

    std::string joined(const std::vector<std::string>& parts, std::string_view between)
    {
      std::string out;
      for (std::size_t i = 0; i < parts.size(); ++i) {
        if (i > 0)
          out += between;
        out += parts[i];
      }
      return out;
    }

    // `f` of every one of `types`, joined.
    template<typename F>
    std::string each_type(const std::vector<const Type*>& types, std::string_view between, F f)
    {
      std::vector<std::string> parts;
      for (auto t : types)
        parts.push_back(f(t));
      return joined(parts, between);
    }

    // A unit out of exponents gathered together, the ones that cancel dropped.
    const Unit* made_of(std::map<std::string, std::int64_t> powers,
                        const std::vector<std::pair<std::string, std::int64_t>>& more, std::int64_t sign,
                        Rational scale)
    {
      for (const auto& [n, e] : more)
        powers[n] += sign * e;
      std::vector<std::pair<std::string, std::int64_t>> kept;
      for (const auto& [n, e] : powers)
        if (e != 0)
          kept.emplace_back(n, e);
      return Unit::make(std::move(kept), scale);
    }

    std::string python_tuple(const std::optional<std::vector<int>>& v)
    {
      if (! v)
        return "None";
      if (v->size() == 1)
        return "(" + std::to_string((*v)[0]) + ",)";
      std::string out = "(";
      for (std::size_t i = 0; i < v->size(); ++i) {
        if (i > 0)
          out += ", ";
        out += std::to_string((*v)[i]);
      }
      return out + ")";
    }

    // `ty` as a program writes it, with the lifetime name in `marks` on each
    // reference standing at that part, counted from `base`.
    std::string marked(const Type* ty, const std::map<int, std::string>& marks, int base = 0)
    {
      if (auto ptr = as<PtrType>(ty)) {
        auto it = marks.find(base);
        if (it == marks.end() || ptr->lasting)
          return ptr->written();
        return std::string("&") + (ptr->is_mutable ? "mut " : "") + "\N{WHITE HOURGLASS}" + it->second + " "
               + ptr->pointee->written();
      }
      if (auto tuple = as<TupleType>(ty); tuple != nullptr && ! held_in_memory(tuple)) {
        auto within = parts_within(tuple);
        std::vector<std::string> parts;
        for (std::size_t i = 0; i < tuple->members.size(); ++i)
          parts.push_back(marked(tuple->members[i], marks, base + int(within[i].first)));
        return "\N{LEFT ANGLE BRACKET}" + joined(parts, ", ") + "\N{RIGHT ANGLE BRACKET}";
      }
      return ty->written();
    }

  } // anonymous namespace


  // -- units ------------------------------------------------------------------

  Rational Rational::of(__int128 n, __int128 d)
  {
    if (d < 0) {
      n = -n;
      d = -d;
    }
    auto g = gcd128(n, d);
    if (g > 1) {
      n /= g;
      d /= g;
    }
    return { n, d };
  }


  Rational Rational::power(std::int64_t exponent) const
  {
    Rational out;
    Rational base = exponent < 0 ? of(den, num) : *this;
    for (std::int64_t i = 0; i < (exponent < 0 ? -exponent : exponent); ++i)
      out = out * base;
    return out;
  }


  const Unit* Unit::make(std::vector<std::pair<std::string, std::int64_t>> powers, Rational scale)
  {
    std::lock_guard guard(store().lock);
    auto& found = store().units[std::pair(powers, scale)];
    if (found == nullptr)
      found = std::make_unique<Unit>(Unit{ std::move(powers), scale });
    return found.get();
  }


  const Unit* Unit::none()
  {
    static const Unit* the = make({}, Rational{});
    return the;
  }


  std::string Unit::render() const
  {
    if (powers.empty())
      return "";
    std::vector<std::pair<std::string, std::int64_t>> above, below;
    for (const auto& [n, e] : powers)
      if (e > 0)
        above.emplace_back(n, e);
      else if (e < 0)
        below.emplace_back(n, -e);
    std::string made = above.empty() ? std::string("1") : joined(each(above), "\N{MULTIPLICATION SIGN}");
    for (const auto& one : each(below))
      made += "\N{DIVISION SIGN}" + one;
    return made;
  }


  std::string Unit::mangled() const
  {
    std::string out;
    for (const auto& [n, e] : powers)
      out += n + std::to_string(e);
    return out;
  }


  const Unit* Unit::times(const Unit& other) const
  {
    return made_of({ powers.begin(), powers.end() }, other.powers, 1, scale * other.scale);
  }


  const Unit* Unit::over(const Unit& other) const
  {
    return made_of({ powers.begin(), powers.end() }, other.powers, -1, scale / other.scale);
  }


  const Unit* Unit::raised(std::int64_t exponent) const
  {
    std::vector<std::pair<std::string, std::int64_t>> scaled;
    for (const auto& [n, e] : powers)
      scaled.emplace_back(n, e * exponent);
    return made_of({}, scaled, 1, scale.power(exponent));
  }


  // -- rendering --------------------------------------------------------------

  const IntType* CharType::holder() const noexcept { return u32(); }
  const IntType* SyntaxType::holder() const noexcept { return u64(); }


  std::string IntType::render() const
  {
    std::string made = (is_signed ? "i" : "u") + std::to_string(bits);
    return unit->is_none() ? made : made + " \N{CURRENCY SIGN}" + unit->render();
  }

  std::string IntType::mangled() const { return (is_signed ? "i" : "u") + std::to_string(bits); }

  const IntType* IntType::bare() const { return int_type(bits, is_signed); }


  std::string FloatType::render() const
  {
    std::string made = "f" + std::to_string(bits);
    return unit->is_none() ? made : made + " \N{CURRENCY SIGN}" + unit->render();
  }

  std::string FloatType::mangled() const { return "f" + std::to_string(bits); }

  const FloatType* FloatType::bare() const { return float_type(bits); }


  std::string PtrType::render() const
  {
    return std::string("ptr<") + (is_mutable ? "mut " : "") + (lasting ? "static " : "") + pointee->render() + ">";
  }

  std::string PtrType::written() const
  {
    return std::string("&") + (is_mutable ? "mut " : "") + (lasting ? "static " : "") + pointee->written();
  }

  std::string PtrType::mangled() const
  {
    return std::string("ptr<") + (is_mutable ? "mut " : "") + (lasting ? "static " : "") + pointee->mangled() + ">";
  }


  std::string RunningType::render() const
  {
    return "running<" + element->render() + (given == nullptr ? "" : ", " + given->render())
           + (lasting ? ", lasting" : "") + ">";
  }

  std::string RunningType::written() const
  {
    return "\N{POSITION INDICATOR}\N{RIGHTWARDS PAIRED ARROWS}" + element->written()
           + (given == nullptr ? "" : " \N{LEFTWARDS PAIRED ARROWS} " + given->written())
           + (lasting ? " in \N{APL FUNCTIONAL SYMBOL QUAD}heap" : "");
  }

  std::string RunningType::mangled() const
  {
    return "running<" + element->mangled() + (given == nullptr ? "" : "," + given->mangled())
           + (lasting ? ",lasting" : "") + ">";
  }


  std::string IterType::render() const
  {
    return "\N{RIGHTWARDS PAIRED ARROWS}" + (element != nullptr ? element->render() : "");
  }

  std::string IterType::written() const
  {
    return "\N{RIGHTWARDS PAIRED ARROWS}" + (element != nullptr ? element->written() : "");
  }

  std::string IterType::mangled() const
  {
    return "iter<" + (element != nullptr ? element->mangled() : "") + ">";
  }


  std::string FuncType::said(bool glyphs) const
  {
    std::vector<std::string> words;
    if (listable)
      words.emplace_back("listable");
    if (impure)
      words.emplace_back("impure");
    if (can_ignore)
      words.emplace_back("can_ignore");
    if (words.empty())
      return "";
    return glyphs ? "@[" + joined(words, ", ") + "] " : joined(words, " ") + " ";
  }

  std::string FuncType::render() const
  {
    return said(false) + "fn(" + each_type(params, ", ", [](auto t) { return t->render(); }) + ")"
           + (lasting ? " lasting" : "") + " \N{RIGHTWARDS ARROW} " + ret->render() + (hands_out ? " in arena" : "");
  }

  std::string FuncType::written() const
  {
    // The lifetime names to write it with: which part of each parameter and of
    // the answer carries which, named in order.
    std::vector<std::map<int, std::string>> given(params.size());
    std::map<int, std::string> answered;
    for (std::size_t at = 0; at < borrows.size(); ++at) {
      const auto& [leaf, borrowed] = borrows[at];
      std::string name = ! borrowed.outer.empty() && borrowed.params.empty()
                           ? borrowed.outer[0].second
                           : std::string(1, char('a' + at % 26));
      answered[std::max(leaf, 0)] = name;
      for (std::size_t i = 0; i < borrowed.params.size(); ++i) {
        if (! borrowed.leaves.empty() && i >= borrowed.leaves.size())
          break;
        int param = borrowed.params[i];
        if (param >= int(given.size()))
          continue;
        std::optional<std::vector<int>> leaves = borrowed.leaves.empty() ? std::nullopt : borrowed.leaves[i];
        for (int one : leaves ? *leaves : std::vector<int>{ 0 })
          given[std::size_t(param)].emplace(one, name);
      }
    }
    std::vector<std::string> inner;
    for (std::size_t i = 0; i < params.size(); ++i)
      inner.push_back(marked(params[i], given[i]));
    return said(true) + "fn(" + joined(inner, ", ") + ")" + (lasting ? " in \N{APL FUNCTIONAL SYMBOL QUAD}heap" : "")
           + " \N{RIGHTWARDS ARROW} " + marked(ret, answered) + (hands_out ? " in its own arena" : "");
  }

  std::string FuncType::mangled() const
  {
    std::string out = said(false) + "fn(" + each_type(params, ",", [](auto t) { return t->mangled(); }) + ")"
                      + (lasting ? " lasting" : "") + ret->mangled() + (hands_out ? " in" : "");
    for (const auto& [leaf, borrowed] : borrows) {
      out += " from" + std::to_string(leaf) + ":";
      for (std::size_t i = 0; i < borrowed.params.size(); ++i)
        out += (i > 0 ? "," : "") + std::to_string(borrowed.params[i]);
      for (const auto& one : borrowed.leaves)
        out += ";" + python_tuple(one);
      for (const auto& one : borrowed.outer)
        out += ";" + one.first;
    }
    return out;
  }


  std::string ProductType::render() const
  {
    if (! name.empty())
      return name;
    std::vector<std::string> inner;
    for (const auto& [n, t] : fields)
      inner.push_back(n + ": " + t->render());
    return "{" + joined(inner, ", ") + "}";
  }

  std::string ProductType::mangled() const
  {
    if (! name.empty())
      return name;
    std::vector<std::string> inner;
    for (const auto& [n, t] : fields)
      inner.push_back(n + ":" + t->mangled());
    return "{" + joined(inner, ",") + "}";
  }


  std::string ResultType::render() const { return ok->render() + "?" + (err != nullptr ? err->render() : ""); }

  std::string ResultType::written() const
  {
    return err == nullptr ? ok->written() + "?" : ok->written() + " ? " + err->written();
  }

  std::string ResultType::mangled() const { return ok->mangled() + "?" + (err != nullptr ? err->mangled() : ""); }


  std::string TupleType::render() const
  {
    return "\N{LEFT ANGLE BRACKET}" + each_type(members, ", ", [](auto t) { return t->render(); })
           + "\N{RIGHT ANGLE BRACKET}";
  }

  std::string TupleType::written() const
  {
    return "\N{LEFT ANGLE BRACKET}" + each_type(members, ", ", [](auto t) { return t->written(); })
           + "\N{RIGHT ANGLE BRACKET}";
  }

  std::string TupleType::mangled() const
  {
    return "tuple<" + each_type(members, ",", [](auto t) { return t->mangled(); }) + ">";
  }


  bool ArrayType::fixed() const noexcept
  {
    return std::ranges::all_of(shape, [](const auto& along) { return along.has_value(); });
  }

  std::optional<std::uint64_t> ArrayType::count() const noexcept
  {
    if (! fixed())
      return std::nullopt;
    std::uint64_t total = 1;
    for (const auto& along : shape)
      total *= *along;
    return total;
  }

  std::string ArrayType::dimensions() const
  {
    std::vector<std::string> parts;
    for (const auto& along : shape)
      parts.push_back(along ? std::to_string(*along) : std::string());
    return joined(parts, ",");
  }

  std::string ArrayType::render() const
  {
    return element->render() + "\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}" + dimensions()
           + "\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}";
  }

  std::string ArrayType::written() const
  {
    return element->written() + "\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}" + dimensions()
           + "\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}";
  }

  std::string ArrayType::mangled() const { return "array<" + element->mangled() + "," + dimensions() + ">"; }


  std::string VecType::render() const { return element->render() + "\N{MULTIPLICATION SIGN}" + std::to_string(lanes); }

  std::string VecType::written() const
  {
    return element->written() + "\N{MULTIPLICATION SIGN}" + std::to_string(lanes);
  }

  std::string VecType::mangled() const { return "vec<" + element->mangled() + "," + std::to_string(lanes) + ">"; }


  std::string SetType::render() const
  {
    return std::string(is_mutable ? "mut " : "") + "\N{LEFT DOUBLE PARENTHESIS}" + element->render()
           + "\N{RIGHT DOUBLE PARENTHESIS}";
  }

  std::string SetType::written() const
  {
    return std::string(is_mutable ? "mut " : "") + "\N{LEFT DOUBLE PARENTHESIS}" + element->written()
           + "\N{RIGHT DOUBLE PARENTHESIS}";
  }

  std::string SetType::mangled() const
  {
    return std::string("set<") + (is_mutable ? "mut " : "") + element->mangled() + ">";
  }


  std::string DictType::render() const
  {
    return std::string(is_mutable ? "mut " : "") + "\N{LEFT DOUBLE PARENTHESIS}" + key->render() + ": "
           + value->render() + "\N{RIGHT DOUBLE PARENTHESIS}";
  }

  std::string DictType::written() const
  {
    return std::string(is_mutable ? "mut " : "") + "\N{LEFT DOUBLE PARENTHESIS}" + key->written() + ": "
           + value->written() + "\N{RIGHT DOUBLE PARENTHESIS}";
  }

  std::string DictType::mangled() const
  {
    return std::string("dict<") + (is_mutable ? "mut " : "") + key->mangled() + "," + value->mangled() + ">";
  }


  std::string EnumType::render() const { return ! name.empty() ? name : "enum<" + joined(members, ", ") + ">"; }

  std::string EnumType::mangled() const { return ! name.empty() ? name : "enum<" + joined(members, ",") + ">"; }

  std::optional<std::size_t> EnumType::index_of(std::string_view member) const
  {
    auto it = std::ranges::find(members, member);
    return it == members.end() ? std::nullopt : std::optional(std::size_t(it - members.begin()));
  }


  std::string SumType::render() const
  {
    if (! name.empty())
      return name;
    std::vector<std::string> inner;
    for (const auto& [n, t] : variants)
      inner.push_back(n + ": " + t->render());
    return "<" + joined(inner, " | ") + ">";
  }

  std::string SumType::mangled() const
  {
    if (! name.empty())
      return name;
    std::vector<std::string> inner;
    for (const auto& [n, t] : variants)
      inner.push_back(n + ":" + t->mangled());
    return "<" + joined(inner, "|") + ">";
  }


  // -- making types -----------------------------------------------------------

  const VoidType* void_type() { static const VoidType the; return &the; }
  const BoolType* bool_type() { static const BoolType the; return &the; }
  const CharType* char_type() { static const CharType the; return &the; }
  const SyntaxType* syntax_type() { static const SyntaxType the; return &the; }
  const MemType* mem_type() { static const MemType the; return &the; }
  const ErrorType* error_type() { static const ErrorType the; return &the; }
  const StrType* str_type() { static const StrType the; return &the; }


  namespace {

    std::size_t mix(std::size_t seed, std::size_t value) noexcept
    {
      return seed ^ (value + 0x9e3779b97f4a7c15ull + (seed << 6) + (seed >> 2));
    }

    // The interned type equal to `probe`, which becomes it where there is none.
    template<typename T>
    const T* intern(std::unique_ptr<T> probe)
    {
      std::lock_guard guard(store().lock);
      auto hash = structural_hash(probe.get());
      auto [first, last] = store().interned.equal_range(hash);
      for (; first != last; ++first)
        if (same(first->second, probe.get()))
          return static_cast<const T*>(first->second);
      const T* raw = probe.get();
      store().kept.push_back(std::move(probe));
      store().interned.emplace(hash, raw);
      return raw;
    }

    // A type of its own, equal perhaps to an interned one and not it.
    template<typename T>
    const T* keep(std::unique_ptr<T> made)
    {
      std::lock_guard guard(store().lock);
      const T* raw = made.get();
      store().kept.push_back(std::move(made));
      return raw;
    }

  } // anonymous namespace


  std::size_t structural_hash(const Type* ty)
  {
    if (ty == nullptr)
      return 0;
    std::size_t h = std::size_t(ty->kind) * 0x100000001b3ull;
    switch (ty->kind) {
    case TypeKind::int_: {
      auto i = static_cast<const IntType*>(ty);
      return mix(mix(mix(h, i->bits), i->is_signed), std::hash<const void*>()(i->unit));
    }
    case TypeKind::float_: {
      auto f = static_cast<const FloatType*>(ty);
      return mix(mix(h, f->bits), std::hash<const void*>()(f->unit));
    }
    case TypeKind::ptr: {
      auto p = static_cast<const PtrType*>(ty);
      return mix(mix(mix(h, structural_hash(p->pointee)), p->is_mutable), p->lasting);
    }
    case TypeKind::frame:
    case TypeKind::opaque:
    case TypeKind::product:
    case TypeKind::sum:
      return mix(h, std::hash<const void*>()(ty));
    case TypeKind::running: {
      auto r = static_cast<const RunningType*>(ty);
      return mix(mix(mix(h, structural_hash(r->element)), structural_hash(r->given)), r->lasting);
    }
    case TypeKind::iter:
      return mix(h, structural_hash(static_cast<const IterType*>(ty)->element));
    case TypeKind::func: {
      auto f = static_cast<const FuncType*>(ty);
      for (auto p : f->params)
        h = mix(h, structural_hash(p));
      return mix(mix(h, structural_hash(f->ret)), f->listable + 2 * f->hands_out + 4 * f->lasting + 8 * f->impure);
    }
    case TypeKind::result: {
      auto r = static_cast<const ResultType*>(ty);
      return mix(mix(h, structural_hash(r->ok)), structural_hash(r->err));
    }
    case TypeKind::tuple:
      for (auto m : static_cast<const TupleType*>(ty)->members)
        h = mix(h, structural_hash(m));
      return h;
    case TypeKind::array: {
      auto a = static_cast<const ArrayType*>(ty);
      h = mix(h, structural_hash(a->element));
      for (const auto& along : a->shape)
        h = mix(h, along ? *along + 1 : 0);
      return h;
    }
    case TypeKind::vec: {
      auto v = static_cast<const VecType*>(ty);
      return mix(mix(h, structural_hash(v->element)), v->lanes);
    }
    case TypeKind::list:
      return mix(h, structural_hash(static_cast<const ListType*>(ty)->element));
    case TypeKind::cursor:
      return mix(h, structural_hash(static_cast<const CursorType*>(ty)->element));
    case TypeKind::set: {
      auto s = static_cast<const SetType*>(ty);
      return mix(mix(h, structural_hash(s->element)), s->is_mutable);
    }
    case TypeKind::dict: {
      auto d = static_cast<const DictType*>(ty);
      return mix(mix(mix(h, structural_hash(d->key)), structural_hash(d->value)), d->is_mutable);
    }
    case TypeKind::enum_: {
      auto e = static_cast<const EnumType*>(ty);
      return mix(h, std::hash<std::string>()(e->name));
    }
    default:
      return h;
    }
  }


  bool same(const Type* a, const Type* b)
  {
    if (a == b)
      return true;
    if (a == nullptr || b == nullptr || a->kind != b->kind)
      return false;
    switch (a->kind) {
    case TypeKind::int_: {
      auto x = static_cast<const IntType*>(a), y = static_cast<const IntType*>(b);
      return x->bits == y->bits && x->is_signed == y->is_signed && x->unit == y->unit;
    }
    case TypeKind::float_: {
      auto x = static_cast<const FloatType*>(a), y = static_cast<const FloatType*>(b);
      return x->bits == y->bits && x->unit == y->unit;
    }
    case TypeKind::ptr: {
      auto x = static_cast<const PtrType*>(a), y = static_cast<const PtrType*>(b);
      return x->is_mutable == y->is_mutable && x->lasting == y->lasting && same(x->pointee, y->pointee);
    }
    case TypeKind::frame:
    case TypeKind::opaque:
    case TypeKind::product:
    case TypeKind::sum:
      return false;
    case TypeKind::running: {
      auto x = static_cast<const RunningType*>(a), y = static_cast<const RunningType*>(b);
      return x->lasting == y->lasting && same(x->element, y->element) && same(x->given, y->given);
    }
    case TypeKind::iter:
      return same(static_cast<const IterType*>(a)->element, static_cast<const IterType*>(b)->element);
    case TypeKind::func: {
      auto x = static_cast<const FuncType*>(a), y = static_cast<const FuncType*>(b);
      return x->params.size() == y->params.size()
             && std::ranges::equal(x->params, y->params, [](auto p, auto q) { return same(p, q); })
             && same(x->ret, y->ret) && x->listable == y->listable && x->hands_out == y->hands_out
             && x->lasting == y->lasting && x->impure == y->impure && x->can_ignore == y->can_ignore
             && x->borrows == y->borrows;
    }
    case TypeKind::result: {
      auto x = static_cast<const ResultType*>(a), y = static_cast<const ResultType*>(b);
      return same(x->ok, y->ok) && same(x->err, y->err);
    }
    case TypeKind::tuple: {
      const auto& x = static_cast<const TupleType*>(a)->members;
      const auto& y = static_cast<const TupleType*>(b)->members;
      return x.size() == y.size() && std::ranges::equal(x, y, [](auto p, auto q) { return same(p, q); });
    }
    case TypeKind::array: {
      auto x = static_cast<const ArrayType*>(a), y = static_cast<const ArrayType*>(b);
      return x->shape == y->shape && same(x->element, y->element);
    }
    case TypeKind::vec: {
      auto x = static_cast<const VecType*>(a), y = static_cast<const VecType*>(b);
      return x->lanes == y->lanes && same(x->element, y->element);
    }
    case TypeKind::list:
      return same(static_cast<const ListType*>(a)->element, static_cast<const ListType*>(b)->element);
    case TypeKind::cursor:
      return same(static_cast<const CursorType*>(a)->element, static_cast<const CursorType*>(b)->element);
    case TypeKind::set: {
      auto x = static_cast<const SetType*>(a), y = static_cast<const SetType*>(b);
      return x->is_mutable == y->is_mutable && same(x->element, y->element);
    }
    case TypeKind::dict: {
      auto x = static_cast<const DictType*>(a), y = static_cast<const DictType*>(b);
      return x->is_mutable == y->is_mutable && same(x->key, y->key) && same(x->value, y->value);
    }
    case TypeKind::enum_: {
      auto x = static_cast<const EnumType*>(a), y = static_cast<const EnumType*>(b);
      return x->members == y->members && x->values == y->values && same(x->holder, y->holder) && x->flag == y->flag
             && x->name == y->name && x->origin == y->origin;
    }
    default:
      // The ones with nothing in them: one of a kind is any of that kind.
      return true;
    }
  }


  const IntType* int_type(unsigned bits, bool is_signed, const Unit* unit)
  {
    if (unit == nullptr)
      unit = Unit::none();
    std::lock_guard guard(store().lock);
    auto& found = store().ints[std::tuple(bits, is_signed, unit)];
    if (found == nullptr)
      found = std::make_unique<IntType>(bits, is_signed, unit);
    return found.get();
  }

  const FloatType* float_type(unsigned bits, const Unit* unit)
  {
    if (unit == nullptr)
      unit = Unit::none();
    std::lock_guard guard(store().lock);
    auto& found = store().floats[std::pair(bits, unit)];
    if (found == nullptr)
      found = std::make_unique<FloatType>(bits, unit);
    return found.get();
  }

  const PtrType* ptr_type(const Type* pointee, bool is_mutable, bool lasting)
  {
    return intern(std::make_unique<PtrType>(pointee, is_mutable, lasting));
  }

  const RunningType* running_type(const Type* element, const Type* given, bool lasting)
  {
    return intern(std::make_unique<RunningType>(element, given, lasting));
  }

  const IterType* iter_type(const Type* element)
  {
    return intern(std::make_unique<IterType>(element));
  }

  const ResultType* result_type(const Type* ok, const Type* err)
  {
    return intern(std::make_unique<ResultType>(ok, err));
  }

  const TupleType* tuple_type(std::vector<const Type*> members)
  {
    return intern(std::make_unique<TupleType>(std::move(members)));
  }

  const ArrayType* array_type(const Type* element, std::vector<std::optional<std::uint64_t>> shape)
  {
    return intern(std::make_unique<ArrayType>(element, std::move(shape)));
  }

  const VecType* vec_type(const Type* element, unsigned lanes)
  {
    return intern(std::make_unique<VecType>(element, lanes));
  }

  const ListType* list_type(const Type* element)
  {
    return intern(std::make_unique<ListType>(element));
  }

  const CursorType* cursor_type(const Type* element)
  {
    return intern(std::make_unique<CursorType>(element));
  }

  const SetType* set_type(const Type* element, bool is_mutable)
  {
    return intern(std::make_unique<SetType>(element, is_mutable));
  }

  const DictType* dict_type(const Type* key, const Type* value, bool is_mutable)
  {
    return intern(std::make_unique<DictType>(key, value, is_mutable));
  }

  namespace {

    std::unique_ptr<FuncType> function_of(std::vector<const Type*> params, const Type* ret, const FuncType& shape)
    {
      auto made = std::make_unique<FuncType>();
      made->params = std::move(params);
      made->ret = ret;
      made->listable = shape.listable;
      made->hands_out = shape.hands_out;
      made->lasting = shape.lasting;
      made->impure = shape.impure;
      made->can_ignore = shape.can_ignore;
      made->borrows = shape.borrows;
      return made;
    }

  } // anonymous namespace

  const FuncType* func_type(std::vector<const Type*> params, const Type* ret, const FuncType& shape)
  {
    return intern(function_of(std::move(params), ret, shape));
  }

  const FuncType* func_type(std::vector<const Type*> params, const Type* ret)
  {
    static const FuncType plain;
    return func_type(std::move(params), ret, plain);
  }


  const PtrType* element_pointer(const Type* element)
  {
    std::lock_guard guard(store().lock);
    auto hash = structural_hash(element);
    auto [first, last] = store().element_pointers.equal_range(hash);
    for (; first != last; ++first)
      if (same(first->second->pointee, element))
        return first->second;
    auto made = keep(std::make_unique<PtrType>(element, true, false));
    store().element_pointers.emplace(hash, made);
    return made;
  }


  const PtrType* stray_ptr_type(const Type* pointee, bool is_mutable, bool lasting)
  {
    return keep(std::make_unique<PtrType>(pointee, is_mutable, lasting));
  }

  const ListType* stray_list_type(const Type* element)
  {
    return keep(std::make_unique<ListType>(element));
  }

  const Type* stray_copy(const Type* ty)
  {
    switch (ty->kind) {
    case TypeKind::ptr: {
      auto p = static_cast<const PtrType*>(ty);
      return keep(std::make_unique<PtrType>(p->pointee, p->is_mutable, p->lasting));
    }
    case TypeKind::running: {
      auto r = static_cast<const RunningType*>(ty);
      return keep(std::make_unique<RunningType>(r->element, r->given, r->lasting));
    }
    case TypeKind::iter:
      return keep(std::make_unique<IterType>(static_cast<const IterType*>(ty)->element));
    case TypeKind::func: {
      auto f = static_cast<const FuncType*>(ty);
      return keep(function_of(f->params, f->ret, *f));
    }
    case TypeKind::result: {
      auto r = static_cast<const ResultType*>(ty);
      return keep(std::make_unique<ResultType>(r->ok, r->err));
    }
    case TypeKind::tuple:
      return keep(std::make_unique<TupleType>(static_cast<const TupleType*>(ty)->members));
    case TypeKind::array: {
      auto a = static_cast<const ArrayType*>(ty);
      return keep(std::make_unique<ArrayType>(a->element, a->shape));
    }
    case TypeKind::vec: {
      auto v = static_cast<const VecType*>(ty);
      return keep(std::make_unique<VecType>(v->element, v->lanes));
    }
    case TypeKind::list:
      return keep(std::make_unique<ListType>(static_cast<const ListType*>(ty)->element));
    case TypeKind::cursor:
      return keep(std::make_unique<CursorType>(static_cast<const CursorType*>(ty)->element));
    case TypeKind::set: {
      auto s = static_cast<const SetType*>(ty);
      return keep(std::make_unique<SetType>(s->element, s->is_mutable));
    }
    case TypeKind::dict: {
      auto d = static_cast<const DictType*>(ty);
      return keep(std::make_unique<DictType>(d->key, d->value, d->is_mutable));
    }
    default:
      return ty;
    }
  }

  const EnumType* enum_type(std::vector<std::string> members, std::vector<std::int64_t> values, const IntType* holder,
                            bool flag, std::string name, std::string origin)
  {
    auto made = std::make_unique<EnumType>();
    made->members = std::move(members);
    made->values = std::move(values);
    made->holder = holder;
    made->flag = flag;
    made->name = std::move(name);
    made->origin = std::move(origin);
    return intern(std::move(made));
  }


  namespace {

    template<typename T, typename... Args>
    T* nominal(Args&&... args)
    {
      std::lock_guard guard(store().lock);
      auto made = std::make_unique<T>(std::forward<Args>(args)...);
      T* raw = made.get();
      store().kept.push_back(std::move(made));
      return raw;
    }

  } // anonymous namespace

  ProductType* new_product() { return nominal<ProductType>(); }
  SumType* new_sum() { return nominal<SumType>(); }
  CoroutineFrame* new_frame(std::string name) { return nominal<CoroutineFrame>(std::move(name)); }
  OpaqueType* new_opaque(std::string name) { return nominal<OpaqueType>(std::move(name)); }


  ProductType* named_record(const std::string& name)
  {
    std::lock_guard guard(store().lock);
    auto& found = store().named[name];
    if (found == nullptr) {
      found = new_product();
      found->name = name;
    }
    return found;
  }


  const IntType* holder_for(std::uint64_t count)
  {
    if (count <= (std::uint64_t(1) << 8))
      return int_type(8, false);
    if (count <= (std::uint64_t(1) << 16))
      return int_type(16, false);
    if (count <= (std::uint64_t(1) << 32))
      return int_type(32, false);
    return int_type(64, false);
  }


  const IntType* u8() { return int_type(8, false); }
  const IntType* u32() { return int_type(32, false); }
  const IntType* u64() { return int_type(64, false); }
  const IntType* i64() { return int_type(64, true); }
  const FloatType* f64() { return float_type(64); }


  const ProductType* arena_type()
  {
    static const ProductType* the = [] {
      auto made = new_product();
      made->fields = { { "next", u64() }, { "limit", u64() }, { "chunk", u64() } };
      made->name = "arena";
      made->unique = true;
      return made;
    }();
    return the;
  }


  const EnumType* narrowing_type()
  {
    static const EnumType* the = enum_type({ "overflow", "underflow", "sign", "absent" }, { 0, 1, 2, 3 }, u8(), false,
                                           "\N{APL FUNCTIONAL SYMBOL QUAD}narrowing", "");
    return the;
  }


  const Type* builtin_type(std::string_view name)
  {
    if (name.size() >= 2 && (name[0] == 'u' || name[0] == 'i')) {
      unsigned bits = 0;
      bool digits = true;
      for (char c : name.substr(1))
        if (c >= '0' && c <= '9')
          bits = bits * 10 + unsigned(c - '0');
        else
          digits = false;
      bool is_signed = name[0] == 'i';
      if (digits && name[1] != '0' && ((bits >= (is_signed ? 2u : 1u) && bits <= 32) || bits == 64))
        return int_type(bits, is_signed);
    }
    if (name == "f32")
      return float_type(32);
    if (name == "f64")
      return float_type(64);
    if (name == "bool")
      return bool_type();
    if (name == "char")
      return char_type();
    if (name == "str")
      return str_type();
    if (name == "void")
      return void_type();
    if (name == "arena")
      return arena_type();
    if (name == "\N{APL FUNCTIONAL SYMBOL QUAD}narrowing")
      return narrowing_type();
    if (name == "syntax")
      return syntax_type();
    return nullptr;
  }


  const Type* without_units(const Type* ty)
  {
    if (auto i = as<IntType>(ty))
      return i->bare();
    if (auto f = as<FloatType>(ty))
      return f->bare();
    return ty;
  }


  // -- what a value is made of ------------------------------------------------

  namespace {

    bool held(const Type* ty, std::unordered_set<const Type*>& seen)
    {
      if (is<SumType>(ty))
        return true;
      if (auto array = as<ArrayType>(ty))
        return array->fixed();
      if (! seen.insert(ty).second)
        return false;
      if (auto product = as<ProductType>(ty))
        return std::ranges::any_of(product->fields, [&](const auto& f) { return held(f.second, seen); });
      if (auto tuple = as<TupleType>(ty))
        return std::ranges::any_of(tuple->members, [&](auto m) { return held(m, seen); });
      return false;
    }

  } // anonymous namespace


  bool held_in_memory(const Type* ty)
  {
    std::unordered_set<const Type*> seen;
    return held(ty, seen);
  }


  std::vector<const Type*> parts_of(const Type* ty)
  {
    if ((is<TupleType>(ty) || is<ProductType>(ty)) && held_in_memory(ty))
      return { ty };
    if (auto result = as<ResultType>(ty)) {
      if (result->err == nullptr)
        return { result->ok, bool_type() };
      return { result->ok, bool_type(), result->err };
    }
    std::vector<const Type*> out;
    if (auto tuple = as<TupleType>(ty)) {
      for (auto member : tuple->members)
        std::ranges::copy(parts_of(member), std::back_inserter(out));
      return out;
    }
    if (auto product = as<ProductType>(ty)) {
      for (const auto& [_, field] : product->fields)
        std::ranges::copy(parts_of(field), std::back_inserter(out));
      return out;
    }
    if (auto array = as<ArrayType>(ty); array != nullptr && ! array->fixed()) {
      out.push_back(element_pointer(array->element));
      for (std::size_t i = 0; i < 2 * array->rank(); ++i)
        out.push_back(u64());
      return out;
    }
    // What pypl4g answers these with are pointers of a cache of its own,
    // equal to the context's and not them, and so they are here.
    if (auto list = as<ListType>(ty))
      return { element_pointer(list->element), u64(), element_pointer(arena_type()) };
    if (auto cursor = as<CursorType>(ty))
      return { element_pointer(stray_list_type(cursor->element)), u64() };
    if (is<IterType>(ty))
      return { element_pointer(u8()), element_pointer(u8()), u64() };
    if (is<RunningType>(ty))
      return { element_pointer(u8()), element_pointer(u8()), element_pointer(u64()), element_pointer(u8()) };
    if (is<FuncType>(ty))
      return { element_pointer(u8()), element_pointer(u8()), element_pointer(arena_type()) };
    if (is<StrType>(ty))
      return { element_pointer(u8()), u64(), element_pointer(arena_type()) };
    return { ty };
  }


  bool made_of_parts(const Type* ty)
  {
    auto parts = parts_of(ty);
    return ! (parts.size() == 1 && same(parts[0], ty));
  }


  std::vector<const Type*> leaves_of(const Type* ty)
  {
    if (auto result = as<ResultType>(ty)) {
      auto out = leaves_of(result->ok);
      out.push_back(bool_type());
      if (result->err != nullptr)
        std::ranges::copy(leaves_of(result->err), std::back_inserter(out));
      return out;
    }
    return parts_of(ty);
  }


  std::vector<std::pair<std::size_t, std::size_t>> parts_within(const Type* ty)
  {
    std::vector<const Type*> held;
    if (auto tuple = as<TupleType>(ty))
      held = tuple->members;
    else if (auto product = as<ProductType>(ty))
      for (const auto& [_, field] : product->fields)
        held.push_back(field);
    else
      return {};
    std::vector<std::pair<std::size_t, std::size_t>> found;
    std::size_t at = 0;
    for (auto one : held) {
      auto count = parts_of(one).size();
      found.emplace_back(at, count);
      at += count;
    }
    return found;
  }

} // namespace pl4g::ir
