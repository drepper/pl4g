#include "comptime/evaluate.hh"

#include <cmath>
#include <format>
#include <limits>
#include <string>

#include "diag/engine.hh"
#include "ir/printer.hh"
#include "util/pyfloat.hh"
#include "util/reflect.hh"
#include "util/unicode.hh"


namespace pl4g::comptime {

  namespace {

    // The names of the language this asks about.
    constexpr std::string_view environ_name = "\N{APL FUNCTIONAL SYMBOL QUAD}environ";
    constexpr std::string_view fields_name = "\N{APL FUNCTIONAL SYMBOL QUAD}fields";
    constexpr std::string_view static_name = "\N{APL FUNCTIONAL SYMBOL QUAD}static";

    // What a refusal of a number beyond 128 bits says.
    constexpr std::string_view too_large_detail = "a number too large for the compiler to work out";

    constexpr __int128 int128_max = std::numeric_limits<__int128>::max();
    constexpr __int128 int128_min = std::numeric_limits<__int128>::min();


    // A function answered.
    struct Return {
      Value value;
    };

    // A loop was left.
    struct Break {
      const ast::Label* label;
      Value value;
    };

    // A loop was begun again.
    struct Continue {
      const ast::Label* label;
    };

    // An operator over values it is not for, which the caller reports.
    struct NotForThis {};

    // A whole number beyond 128 bits.
    struct TooLarge {};


    // Whether a jump naming `label` is about the loop called `loop`.
    //
    // pypl4g compares the two `Label` nodes, span and all, which never agree
    // -- the label on the jump is written somewhere else than the label on the
    // loop -- so a labelled jump there escapes every loop and stops the
    // compiler.  Here the names are what is compared, which is what the
    // language says.
    bool names(const ast::Label* label, const ast::Label* loop)
    {
      return label == nullptr || (loop != nullptr && label->name == loop->name);
    }


    // `value` as a double, for a comparison or an operator with a float.
    double as_double(const Value& value)
    {
      if (auto d = value.get<double>())
        return *d;
      if (auto b = value.get<bool>())
        return *b ? 1.0 : 0.0;
      return static_cast<double>(*value.get<__int128>());
    }

    // The whole number a bool, an int or an integral float is, if it is one.
    std::optional<__int128> as_whole(const Value& value)
    {
      if (auto n = value.get<__int128>())
        return *n;
      if (auto b = value.get<bool>())
        return *b ? 1 : 0;
      if (auto d = value.get<double>())
        if (std::isfinite(*d) && *d == std::trunc(*d) && *d >= -0x1p127 && *d < 0x1p127)
          return static_cast<__int128>(*d);
      return std::nullopt;
    }

    // Whether a value is one of Python's numbers, `bool` among them.
    bool numeric(const Value& value)
    {
      return value.is<bool>() || value.is<__int128>() || value.is<double>();
    }

    // Python's `==` between two numbers.
    bool numbers_equal(const Value& a, const Value& b)
    {
      if (a.is<double>() || b.is<double>()) {
        if (a.is<double>() && b.is<double>())
          return *a.get<double>() == *b.get<double>();
        auto whole = as_whole(a.is<double>() ? b : a);
        auto other = as_whole(a.is<double>() ? a : b);
        return whole.has_value() && other.has_value() && *whole == *other;
      }
      return *as_whole(a) == *as_whole(b);
    }

    // Python's `<` between two numbers.
    bool numbers_less(const Value& a, const Value& b)
    {
      if (a.is<double>() || b.is<double>())
        return as_double(a) < as_double(b);
      return *as_whole(a) < *as_whole(b);
    }

    // A hash agreeing with `equal` over what a table may hold as a key.
    std::size_t hash_of(const Value& key)
    {
      if (numeric(key)) {
        if (auto whole = as_whole(key))
          return std::hash<std::uint64_t>{}(static_cast<std::uint64_t>(*whole))
                 ^ std::hash<std::uint64_t>{}(static_cast<std::uint64_t>(*whole >> 64));
        return std::hash<double>{}(*key.get<double>());
      }
      if (auto s = key.get<std::string>())
        return std::hash<std::string>{}(*s);
      if (auto c = key.get<const ir::Value*>())
        return std::hash<const void*>{}(*c);
      if (key.is_missing())
        return 2;
      return 1;
    }

    // Python's `repr` of text.
    std::string repr_text(std::string_view text)
    {
      bool single = text.find('\'') != std::string_view::npos;
      bool dbl = text.find('"') != std::string_view::npos;
      char quote = single && ! dbl ? '"' : '\'';
      std::string out(1, quote);
      for (std::size_t at = 0; at < text.size();) {
        auto [cp, length] = unicode::decode(text, at);
        at += length;
        if (cp == U'\\' || cp == static_cast<char32_t>(quote)) {
          out += '\\';
          out += static_cast<char>(cp);
        } else if (cp == U'\t')
          out += "\\t";
        else if (cp == U'\n')
          out += "\\n";
        else if (cp == U'\r')
          out += "\\r";
        else if (cp < 0x20 || (cp >= 0x7f && cp < 0xa0))
          out += std::format("\\x{:02x}", static_cast<unsigned>(cp));
        else
          unicode::append(out, cp);
      }
      out += quote;
      return out;
    }

    // The characters of `text`, each a text of its own.
    std::vector<Value> characters(std::string_view text)
    {
      std::vector<Value> out;
      for (std::size_t at = 0; at < text.size();) {
        auto length = unicode::decode(text, at).length;
        out.emplace_back(text.substr(at, length));
        at += length;
      }
      return out;
    }

    // The character of `text` at `index`, counted in characters.
    std::string character_at(std::string_view text, std::size_t index)
    {
      std::size_t at = 0;
      for (; index > 0; --index)
        at += unicode::decode(text, at).length;
      return std::string(text.substr(at, unicode::decode(text, at).length));
    }


    // Overflow-checked whole-number arithmetic.
    __int128 add(__int128 a, __int128 b)
    {
      __int128 r;
      if (__builtin_add_overflow(a, b, &r)) [[unlikely]]
        throw TooLarge{};
      return r;
    }

    __int128 sub(__int128 a, __int128 b)
    {
      __int128 r;
      if (__builtin_sub_overflow(a, b, &r)) [[unlikely]]
        throw TooLarge{};
      return r;
    }

    __int128 mul(__int128 a, __int128 b)
    {
      __int128 r;
      if (__builtin_mul_overflow(a, b, &r)) [[unlikely]]
        throw TooLarge{};
      return r;
    }

    __int128 magnitude(__int128 a)
    {
      if (a == int128_min) [[unlikely]]
        throw TooLarge{};
      return a < 0 ? -a : a;
    }

    // Python's `a % b` for whole numbers: the sign of the divisor.
    __int128 py_mod(__int128 a, __int128 b)
    {
      if (b == -1)
        return 0;
      __int128 r = a % b;
      return r != 0 && ((r < 0) != (b < 0)) ? r + b : r;
    }

    // And for floats.
    double py_fmod(double a, double b)
    {
      double r = std::fmod(a, b);
      if (r != 0.0) {
        if ((r < 0.0) != (b < 0.0))
          r += b;
      } else
        r = std::copysign(0.0, b);
      return r;
    }


    // A set's own operators, over two tables.
    Value set_operator(ast::BinaryOp op, const Table& left, const Table& right)
    {
      std::vector<Value> keys;
      switch (op) {
      case ast::BinaryOp::bit_or:
        for (const auto& [key, _] : left.entries)
          keys.push_back(key);
        for (const auto& [key, _] : right.entries)
          if (! left.contains(key))
            keys.push_back(key);
        break;
      case ast::BinaryOp::bit_and:
        for (const auto& [key, _] : left.entries)
          if (right.contains(key))
            keys.push_back(key);
        break;
      case ast::BinaryOp::bit_xor:
        for (const auto& [key, _] : left.entries)
          if (! right.contains(key))
            keys.push_back(key);
        for (const auto& [key, _] : right.entries)
          if (! left.contains(key))
            keys.push_back(key);
        break;
      case ast::BinaryOp::subtract:
        for (const auto& [key, _] : left.entries)
          if (! right.contains(key))
            keys.push_back(key);
        break;
      default:
        throw NotForThis{};
      }
      auto made = std::make_shared<Table>(true);
      for (auto& key : keys)
        made->put(std::move(key), true);
      return made;
    }

    // One operator over two values it has already been given.
    Value apply(ast::BinaryOp op, const Value& left, const Value& right)
    {
      using enum ast::BinaryOp;
      if (op == equal)
        return comptime::equal(left, right);
      if (op == not_equal)
        return ! comptime::equal(left, right);
      if (auto l = left.get<std::shared_ptr<Table>>())
        if (auto r = right.get<std::shared_ptr<Table>>())
          if ((*l)->is_set != false && (*r)->is_set != false)
            return set_operator(op, **l, **r);
      if (op == concat) {
        if (left.is<std::string>() && right.is<std::string>())
          return *left.get<std::string>() + *right.get<std::string>();
        if (left.is<std::shared_ptr<Run>>() && right.is<std::shared_ptr<Run>>()) {
          Run joined = **left.get<std::shared_ptr<Run>>();
          const Run& more = **right.get<std::shared_ptr<Run>>();
          joined.insert(joined.end(), more.begin(), more.end());
          return Value::run(std::move(joined));
        }
        throw NotForThis{};
      }
      bool numbers = left.is_number() && right.is_number();
      bool both_strings = left.is<std::string>() && right.is<std::string>();
      if (op == less || op == less_equal || op == greater || op == greater_equal) {
        if (! numbers && ! both_strings) [[unlikely]]
          throw NotForThis{};
        auto compare = [&](auto&& holds) -> bool {
          if (both_strings)
            return holds(*left.get<std::string>(), *right.get<std::string>());
          if (left.is_whole() && right.is_whole())
            return holds(*left.get<__int128>(), *right.get<__int128>());
          return holds(as_double(left), as_double(right));
        };
        switch (op) {
        case less:
          return compare([](const auto& a, const auto& b) { return a < b; });
        case less_equal:
          return compare([](const auto& a, const auto& b) { return a <= b; });
        case greater:
          return compare([](const auto& a, const auto& b) { return a > b; });
        default:
          return compare([](const auto& a, const auto& b) { return a >= b; });
        }
      }
      if (! numbers) [[unlikely]]
        throw NotForThis{};
      bool whole = left.is_whole() && right.is_whole();
      __int128 a = whole ? *left.get<__int128>() : 0;
      __int128 b = whole ? *right.get<__int128>() : 0;
      double x = as_double(left);
      double y = as_double(right);
      switch (op) {
      case add:
      case sat_add:
        return whole ? Value(comptime::add(a, b)) : Value(x + y);
      case subtract:
      case sat_sub:
        return whole ? Value(sub(a, b)) : Value(x - y);
      case multiply:
      case sat_mul:
        return whole ? Value(mul(a, b)) : Value(x * y);
      case divide:
        if (whole) {
          // Toward zero, as the program divides.
          if (b == 0)
            throw DivisionByNought{};
          __int128 quotient = magnitude(a) / magnitude(b);
          return (a < 0) == (b < 0) ? quotient : -quotient;
        }
        if (y == 0.0)
          throw DivisionByNought{};
        return x / y;
      case remainder:
        if (whole) {
          // What is left over carries the sign of what was divided.
          if (b == 0)
            throw DivisionByNought{};
          __int128 quotient = magnitude(a) / magnitude(b);
          return sub(a, mul((a < 0) == (b < 0) ? quotient : -quotient, b));
        }
        if (y == 0.0)
          throw DivisionByNought{};
        return py_fmod(x, y);
      case bit_and:
      case bit_or:
      case bit_xor:
      case shift_left:
      case shift_right:
        // Python has none of these over a float, and a negative shift is an
        // error there too.
        if (! whole) [[unlikely]]
          throw NotForThis{};
        switch (op) {
        case bit_and:
          return a & b;
        case bit_or:
          return a | b;
        case bit_xor:
          return a ^ b;
        case shift_left:
          if (b < 0) [[unlikely]]
            throw NotForThis{};
          if (a == 0)
            return a;
          if (b >= 127 || (a > 0 ? a > (int128_max >> b) : a < (int128_min >> b))) [[unlikely]]
            throw TooLarge{};
          return a * (static_cast<__int128>(1) << b);
        default:
          if (b < 0) [[unlikely]]
            throw NotForThis{};
          return b >= 127 ? (a < 0 ? -1 : 0) : a >> b;
        }
      case max:
        return numbers_less(left, right) ? right : left;
      case min:
        return numbers_less(right, left) ? right : left;
      case power:
        if (! right.is_whole()) [[unlikely]]
          throw NotForThis{};
        if (left.is<double>())
          return comptime::power(x, b);
        {
          auto found = comptime::power(a, b);
          if (found.too_large) [[unlikely]]
            throw TooLarge{};
          return found.value;
        }
      case divides:
      case not_divides: {
        bool even;
        if (whole) {
          if (a == 0)
            throw DivisionByNought{};
          even = py_mod(b, a) == 0;
        } else {
          if (x == 0.0)
            throw DivisionByNought{};
          even = py_fmod(y, x) == 0.0;
        }
        return op == divides ? even : ! even;
      }
      default:
        throw NotForThis{};
      }
    }

    // Whether an `in` names `⎕static`.
    bool in_the_image(const ast::Expr* arena)
    {
      auto name = ast::as<ast::NameRef>(arena);
      return name != nullptr && name->name == static_name;
    }

    // `key` as something a table worked out here may hold it under.
    const Value& hashable(const Value& key, Span span)
    {
      if (key.is<std::shared_ptr<Run>>() || key.is<std::shared_ptr<Table>>() || key.is<std::shared_ptr<Record>>()
          || key.is<Reference>()) [[unlikely]]
        throw CannotEvaluate(span, "a key of this kind");
      return key;
    }

    // How each piece of the language this cannot work out is named in the
    // refusal, by pypl4g's class name.
    std::string described(const ast::Node* node)
    {
      using enum ast::NodeKind;
      switch (node->tag) {
      case Match:
        return "a match";
      case Lambda:
        return "a lambda";
      case Try:
        return "a `?`";
      case Failure:
        return "a failure";
      case Lifted:
        return "a lifted type";
      case SetLit:
        return "a set";
      case DictLit:
        return "a dictionary";
      case Spread:
        return "a spread";
      case Range:
        return "a range outside a loop";
      case Capture:
        return "a capture";
      case Index:
        return "an entry of a collection";
      default: {
        std::string name(enum_name(node->tag));
        for (auto& c : name)
          if (c >= 'A' && c <= 'Z')
            c = static_cast<char>(c - 'A' + 'a');
        return name;
      }
      }
    }

  } // anonymous namespace


  // -- values -----------------------------------------------------------------

  const Value* Record::field(std::string_view name) const
  {
    for (const auto& [n, v] : fields)
      if (n == name)
        return &v;
    return nullptr;
  }


  void Record::set(std::string_view name, Value value)
  {
    for (auto& [n, v] : fields)
      if (n == name) {
        v = std::move(value);
        return;
      }
    fields.emplace_back(std::string(name), std::move(value));
  }


  const Value* Table::find(const Value& key) const
  {
    auto [from, to] = index.equal_range(hash_of(key));
    for (; from != to; ++from)
      if (equal(entries[from->second].first, key))
        return &entries[from->second].second;
    return nullptr;
  }


  void Table::put(Value key, Value value)
  {
    auto hash = hash_of(key);
    auto [from, to] = index.equal_range(hash);
    for (; from != to; ++from)
      if (equal(entries[from->second].first, key)) {
        entries[from->second].second = std::move(value);
        return;
      }
    index.emplace(hash, entries.size());
    entries.emplace_back(std::move(key), std::move(value));
  }


  bool equal(const Value& a, const Value& b)
  {
    if (numeric(a) || numeric(b))
      return numeric(a) && numeric(b) && numbers_equal(a, b);
    if (a.held.index() != b.held.index())
      return false;
    return std::visit([&]<typename T>(const T& left) -> bool {
      const T& right = *b.get<T>();
      if constexpr (std::is_same_v<T, std::shared_ptr<Run>>) {
        if (left == right)
          return true;
        if (left->size() != right->size())
          return false;
        for (std::size_t i = 0; i < left->size(); ++i)
          if (! equal((*left)[i], (*right)[i]))
            return false;
        return true;
      } else if constexpr (std::is_same_v<T, std::shared_ptr<Table>>) {
        if (left == right)
          return true;
        if (left->size() != right->size())
          return false;
        for (const auto& [key, held] : left->entries) {
          auto other = right->find(key);
          if (other == nullptr || ! equal(held, *other))
            return false;
        }
        return true;
      } else if constexpr (std::is_same_v<T, std::shared_ptr<Record>>) {
        if (left == right)
          return true;
        if (left->type_name != right->type_name || left->fields.size() != right->fields.size())
          return false;
        for (const auto& [name, held] : left->fields) {
          auto other = right->field(name);
          if (other == nullptr || ! equal(held, *other))
            return false;
        }
        return true;
      } else if constexpr (std::is_same_v<T, Reference>)
        return left.mutable_ == right.mutable_ && (left.cell == right.cell || equal(left.cell->value, right.cell->value));
      else if constexpr (std::is_same_v<T, std::monostate> || std::is_same_v<T, Missing>)
        return true;
      else
        return left == right;
    }, a.held);
  }


  bool truthy(const Value& v)
  {
    return std::visit([]<typename T>(const T& held) -> bool {
      if constexpr (std::is_same_v<T, std::monostate>)
        return false;
      else if constexpr (std::is_same_v<T, bool>)
        return held;
      else if constexpr (std::is_same_v<T, __int128>)
        return held != 0;
      else if constexpr (std::is_same_v<T, double>)
        return held != 0.0;
      else if constexpr (std::is_same_v<T, std::string>)
        return ! held.empty();
      else if constexpr (std::is_same_v<T, std::shared_ptr<Run>>)
        return ! held->empty();
      else if constexpr (std::is_same_v<T, std::shared_ptr<Table>>)
        return held->size() != 0;
      else
        return true;
    }, v.held);
  }


  std::string to_repr(const Value& v)
  {
    return std::visit([]<typename T>(const T& held) -> std::string {
      if constexpr (std::is_same_v<T, std::monostate>)
        return "None";
      else if constexpr (std::is_same_v<T, Missing>)
        return "\N{UP TACK}";
      else if constexpr (std::is_same_v<T, bool>)
        return held ? "True" : "False";
      else if constexpr (std::is_same_v<T, __int128>)
        return ir::decimal(held);
      else if constexpr (std::is_same_v<T, double>)
        return python_repr(held);
      else if constexpr (std::is_same_v<T, std::string>)
        return repr_text(held);
      else if constexpr (std::is_same_v<T, std::shared_ptr<Run>>) {
        std::string out = "[";
        for (std::size_t i = 0; i < held->size(); ++i)
          out += (i == 0 ? "" : ", ") + to_repr((*held)[i]);
        return out + "]";
      } else if constexpr (std::is_same_v<T, std::shared_ptr<Table>>) {
        std::string out = "{";
        for (std::size_t i = 0; i < held->entries.size(); ++i)
          out += (i == 0 ? "" : ", ") + to_repr(held->entries[i].first) + ": " + to_repr(held->entries[i].second);
        return out + "}";
      } else if constexpr (std::is_same_v<T, std::shared_ptr<Record>>) {
        std::string out = "Record(type_name=" + repr_text(held->type_name) + ", fields={";
        for (std::size_t i = 0; i < held->fields.size(); ++i)
          out += (i == 0 ? "" : ", ") + repr_text(held->fields[i].first) + ": " + to_repr(held->fields[i].second);
        return out + "})";
      } else if constexpr (std::is_same_v<T, Reference>)
        return "Reference(cell=Cell(value=" + to_repr(held.cell->value) + "), mutable="
               + (held.mutable_ ? "True" : "False") + ")";
      else
        return "<EnumConst>";
    }, v.held);
  }


  std::string to_str(const Value& v)
  {
    if (auto s = v.get<std::string>())
      return *s;
    return to_repr(v);
  }


  std::optional<Value> Reflect::fields(const ast::Expr*)
  {
    return std::nullopt;
  }


  std::optional<Value> Reflect::enum_value(const ast::Member*)
  {
    return std::nullopt;
  }


  ast::Integer power(__int128 base, __int128 exponent)
  {
    if (exponent < 0) {
      // One divided by the positive power, toward zero.
      if (base == 0)
        throw DivisionByNought{};
      if (base == 1)
        return { 1, false };
      if (base == -1)
        return { (-exponent) % 2 == 0 ? 1 : -1, false };
      return { 0, false };
    }
    __int128 answer = 1;
    for (__int128 factor = base, left = exponent; left > 0; left >>= 1) {
      if ((left & 1) != 0 && __builtin_mul_overflow(answer, factor, &answer))
        return { 0, true };
      if (left > 1 && __builtin_mul_overflow(factor, factor, &factor))
        return { 0, true };
    }
    return { answer, false };
  }


  double power(double base, __int128 exponent)
  {
    if (exponent >= 0)
      return std::pow(base, static_cast<double>(exponent));
    double divisor = std::pow(base, static_cast<double>(-exponent));
    if (divisor == 0.0)
      throw DivisionByNought{};
    return 1.0 / divisor;
  }


  // -- the evaluator ----------------------------------------------------------

  // What a loop walks: a run of values taken before it begins, or a range,
  // whose values are made as they are wanted.
  struct Evaluator::Turns {
    std::vector<Value> values;
    bool is_range = false;
    __int128 start = 0;
    __int128 stop = 0;
    __int128 step = 1;

    // Hand each turn's value to `f`, until it answers false.
    template<typename F>
    void each(F&& f) const
    {
      if (! is_range) {
        for (const auto& one : values)
          if (! f(one))
            return;
        return;
      }
      for (__int128 at = start; step > 0 ? at < stop : at > stop;) {
        if (! f(Value(at)))
          return;
        if (__builtin_add_overflow(at, step, &at))
          return;
      }
    }
  };


  Evaluator::Evaluator(std::span<const ast::SourceUnit* const> units, Builtins builtins,
                       std::vector<std::pair<std::string, std::string>> environ, Reflect* reflect)
    : reflect(reflect != nullptr ? reflect : &fallback), builtins(std::move(builtins)), environ(std::move(environ))
  {
    for (auto unit : units)
      for (auto item : unit->items)
        if (auto func = ast::as<ast::FuncDef>(item))
          functions[func->name] = func;
        else if (auto var = ast::as<ast::VarDef>(item))
          globals[var->name] = var;
        else if (auto import = ast::as<ast::ModuleImport>(item))
          modules[import->name] = import->source;
  }


  Value Evaluator::call(const ast::FuncDef& func, std::span<const Value> arguments)
  {
    try {
      return call_at(func, arguments, func.name_span);
    } catch (const Break&) {
      throw InternalError("a 'break' left the function it was written in");
    } catch (const Continue&) {
      throw InternalError("a 'continue' left the function it was written in");
    }
  }


  const ast::FuncDef* Evaluator::function(std::string_view name) const
  {
    auto found = functions.find(name);
    return found == functions.end() ? nullptr : found->second;
  }


  Value Evaluator::evaluate(const ast::Expr* expr)
  {
    scopes.emplace_back();
    struct Pop {
      std::vector<Scope>& scopes;
      ~Pop() { scopes.pop_back(); }
    } pop{ scopes };
    try {
      return this->expr(expr);
    } catch (const Break&) {
      throw InternalError("a 'break' left the expression it was written in");
    } catch (const Continue&) {
      throw InternalError("a 'continue' left the expression it was written in");
    }
  }


  // One call, with its own scope and its own depth.
  Value Evaluator::call_at(const ast::FuncDef& func, std::span<const Value> arguments, Span span)
  {
    if (func.body == nullptr) [[unlikely]]
      throw CannotEvaluate(span, std::format("'{}' has no body the compiler can run", func.name));
    if (depth >= depth_allowed) [[unlikely]]
      throw CannotEvaluate(span, std::format("calls went {} deep", depth_allowed), true);
    if (arguments.size() != func.params.size()) [[unlikely]]
      throw CannotEvaluate(span, std::format("'{}' wants {} arguments and this call works out {}", func.name,
                                             func.params.size(), arguments.size()));
    Scope scope;
    for (std::size_t i = 0; i < arguments.size(); ++i)
      scope[func.params[i]->name] = std::make_shared<Cell>(arguments[i]);
    ++depth;
    scopes.push_back(std::move(scope));
    struct Pop {
      Evaluator& self;
      ~Pop()
      {
        self.scopes.pop_back();
        --self.depth;
      }
    } pop{ *this };
    try {
      return block(func.body);
    } catch (Return& answered) {
      return std::move(answered.value);
    }
  }


  // -- statements -------------------------------------------------------------

  // Run every statement, answering with what the last one came to; what a
  // `defer` put off runs as the block is left, the last put off first.
  Value Evaluator::block(const ast::Block* body)
  {
    scopes.emplace_back();
    struct Pop {
      std::vector<Scope>& scopes;
      ~Pop() { scopes.pop_back(); }
    } pop{ scopes };
    std::vector<const ast::Stmt*> later;
    Value answer;
    try {
      for (auto one : body->stmts()) {
        if (auto deferred = ast::as<ast::Defer>(one)) {
          step(deferred->span);
          later.push_back(deferred->stmt);
          continue;
        }
        answer = stmt(one);
      }
    } catch (const Return&) {
      put_off(later);
      throw;
    } catch (const Break&) {
      put_off(later);
      throw;
    } catch (const Continue&) {
      put_off(later);
      throw;
    }
    put_off(later);
    return answer;
  }


  // Run what a block put off, the last first.
  void Evaluator::put_off(const std::vector<const ast::Stmt*>& later)
  {
    for (auto one = later.rbegin(); one != later.rend(); ++one)
      stmt(*one);
  }


  // Run one statement, answering with its value where it has one.
  Value Evaluator::stmt(const ast::Stmt* stmt)
  {
    step(stmt->span);
    using enum ast::NodeKind;
    switch (stmt->tag) {
    case VarDef:
      define(static_cast<const ast::VarDef*>(stmt));
      return {};
    case ModuleImport: {
      auto import = static_cast<const ast::ModuleImport*>(static_cast<const ast::Node*>(stmt));
      modules[import->name] = import->source;
      return {};
    }
    case ExprStmt:
      return expr(static_cast<const ast::ExprStmt*>(stmt)->value);
    case ReturnStmt: {
      auto ret = static_cast<const ast::ReturnStmt*>(stmt);
      throw Return{ ret->value == nullptr ? Value() : expr(ret->value) };
    }
    case AssignStmt:
      assign_name(static_cast<const ast::AssignStmt*>(stmt));
      return {};
    case MemberAssign:
      assign_member(static_cast<const ast::MemberAssign*>(stmt));
      return {};
    case DerefAssign:
      assign_deref(static_cast<const ast::DerefAssign*>(stmt));
      return {};
    case ElementAssign:
      assign_element(static_cast<const ast::ElementAssign*>(stmt));
      return {};
    case EntryAssign:
      assign_entry(static_cast<const ast::EntryAssign*>(stmt));
      return {};
    case Break: {
      auto left = static_cast<const ast::Break*>(stmt);
      throw comptime::Break{ left->label, left->value == nullptr ? Value() : expr(left->value) };
    }
    case Continue:
      throw comptime::Continue{ static_cast<const ast::Continue*>(stmt)->label };
    case EmptyStmt:
      return {};
    default:
      throw refuse(stmt->span, stmt);
    }
  }


  // Bind a name in the innermost scope.
  void Evaluator::define(const ast::VarDef* stmt)
  {
    if (! stmt->more.empty()) [[unlikely]]
      throw CannotEvaluate(stmt->span, "taking a value apart into several names");
    auto made = std::make_shared<Cell>(expr(stmt->value));
    scopes.back()[stmt->name] = std::move(made);
  }


  // `name ← value`, which rebinds what the name stands for.
  void Evaluator::assign_name(const ast::AssignStmt* stmt)
  {
    if (! stmt->more.empty()) [[unlikely]]
      throw CannotEvaluate(stmt->span, "taking a value apart into several names");
    auto given = expr(stmt->value);
    cell_of(stmt->name, stmt->name_span)->value = std::move(given);
  }


  // `record.field ← value`, through a name or through a reference.
  void Evaluator::assign_member(const ast::MemberAssign* stmt)
  {
    auto held = record_of(stmt->base);
    held->set(stmt->name, expr(stmt->value));
  }


  // `reference⌖ ← value`, which writes where the reference points.
  void Evaluator::assign_deref(const ast::DerefAssign* stmt)
  {
    auto found = expr(stmt->target);
    auto ref = found.get<Reference>();
    if (ref == nullptr) [[unlikely]]
      throw CannotEvaluate(stmt->span, "writing through something that is not a reference");
    auto cell = ref->cell;
    cell->value = expr(stmt->value);
  }


  // `array⟦i⟧ ← value`.
  void Evaluator::assign_element(const ast::ElementAssign* stmt)
  {
    auto held = expr(stmt->base);
    if (auto ref = held.get<Reference>()) {
      Value inner = ref->cell->value;
      held = std::move(inner);
    }
    auto run = held.get<std::shared_ptr<Run>>();
    if (run == nullptr || stmt->indices.size() != 1) [[unlikely]]
      throw CannotEvaluate(stmt->span, "writing into this");
    auto keep = *run;
    auto at = as_index(stmt->indices[0], keep->size());
    auto value = expr(stmt->value);
    (*keep)[at] = std::move(value);
  }


  // `d⸨k⸩ ← v`: an entry put in a dictionary being worked out.
  void Evaluator::assign_entry(const ast::EntryAssign* stmt)
  {
    auto held = value(stmt->base);
    auto table = held.get<std::shared_ptr<Table>>();
    if (table == nullptr || (*table)->plain || (*table)->is_set == true) [[unlikely]]
      throw CannotEvaluate(stmt->span, "putting an entry in this");
    auto keep = *table;
    keep->is_set = false;
    auto key = value(stmt->key);
    // Python works out what is put in before it asks whether the key may be one.
    auto entry = value(stmt->value);
    hashable(key, stmt->key->span);
    keep->put(std::move(key), std::move(entry));
  }


  // -- control flow -----------------------------------------------------------

  // The first arm whose condition holds, or the one with none.
  Value Evaluator::if_(const ast::If* expr)
  {
    for (auto arm : expr->arms)
      if (arm->condition == nullptr || truth(arm->condition))
        return block(arm->body);
    return {};
  }


  // A loop, until the condition stops holding or something leaves it.
  //
  // pypl4g reads a loop written `until` as one written `while`, and so does
  // this, to come to what it comes to.
  Value Evaluator::while_(const ast::While* expr)
  {
    while (truth(expr->condition)) {
      step(expr->span);
      try {
        block(expr->body);
      } catch (Break& left) {
        if (! names(left.label, expr->label))
          throw;
        return std::move(left.value);
      } catch (const Continue& again) {
        if (! names(again.label, expr->label))
          throw;
      }
    }
    if (expr->alternative != nullptr)
      return block(expr->alternative);
    return {};
  }


  // A loop over an array, a list, text or a range.
  Value Evaluator::foreach(const ast::ForEach* expr)
  {
    if (! expr->more.empty()) [[unlikely]]
      throw CannotEvaluate(expr->span, "taking each element apart into several names");
    auto over = iterable(expr->iterable);
    std::optional<Value> left_with;
    over.each([&](const Value& one) {
      step(expr->span);
      scopes.push_back(Scope{ { expr->name, std::make_shared<Cell>(one) } });
      struct Pop {
        std::vector<Scope>& scopes;
        ~Pop() { scopes.pop_back(); }
      } pop{ scopes };
      try {
        block(expr->body);
      } catch (Break& left) {
        if (! names(left.label, expr->label))
          throw;
        left_with = std::move(left.value);
        return false;
      } catch (const Continue& again) {
        if (! names(again.label, expr->label))
          throw;
      }
      return true;
    });
    if (left_with.has_value())
      return std::move(*left_with);
    if (expr->alternative != nullptr)
      return block(expr->alternative);
    return {};
  }


  // What a loop walks, worked out before it begins.
  Evaluator::Turns Evaluator::iterable(const ast::Expr* expr)
  {
    if (auto range = ast::as<ast::Range>(expr)) {
      Turns turns;
      turns.is_range = true;
      turns.start = range->start != nullptr ? number(range->start) : 0;
      std::optional<__int128> stop;
      if (range->stop != nullptr)
        stop = number(range->stop);
      turns.step = range->step != nullptr ? number(range->step) : 1;
      if (! stop.has_value()) [[unlikely]]
        throw CannotEvaluate(range->span, "a range with no end");
      if (turns.step == 0) [[unlikely]]
        throw CannotEvaluate(range->span, "a range whose step is nought");
      turns.stop = *stop;
      return turns;
    }
    auto found = this->expr(expr);
    if (auto ref = found.get<Reference>()) {
      Value inner = ref->cell->value;
      found = std::move(inner);
    }
    if (auto run = found.get<std::shared_ptr<Run>>())
      return Turns{ .values = **run };
    if (auto text = found.get<std::string>())
      return Turns{ .values = characters(*text) };
    throw CannotEvaluate(expr->span, "walking over this");
  }


  // -- expressions ------------------------------------------------------------

  // What an expression comes to.
  Value Evaluator::expr(const ast::Expr* expr)
  {
    step(expr->span);
    using enum ast::NodeKind;
    switch (expr->tag) {
    case IntLit: {
      auto lit = static_cast<const ast::IntLit*>(expr);
      if (lit->value.too_large) [[unlikely]]
        throw CannotEvaluate(expr->span, std::string(too_large_detail));
      return lit->value.value;
    }
    case FloatLit:
      return static_cast<const ast::FloatLit*>(expr)->value;
    case BoolLit:
      return static_cast<const ast::BoolLit*>(expr)->value;
    case CharLit:
      return static_cast<const ast::CharLit*>(expr)->value.value;
    case StringLit:
      return static_cast<const ast::StringLit*>(expr)->value;
    case NameRef: {
      auto name = static_cast<const ast::NameRef*>(expr);
      auto cell = cell_of(name->name, name->span);
      return cell->value;
    }
    case Binary:
      return binary(static_cast<const ast::Binary*>(expr));
    case Raised:
      return raised(static_cast<const ast::Raised*>(expr));
    case Unary:
      return unary(static_cast<const ast::Unary*>(expr));
    case Call:
      return call_expr(static_cast<const ast::Call*>(expr));
    case Member:
      return member(static_cast<const ast::Member*>(expr));
    case Element:
      return element(static_cast<const ast::Element*>(expr));
    case Index:
      return index(static_cast<const ast::Index*>(expr));
    case ArrayLit:
    case ListLit:
    case TupleLit: {
      auto elements = expr->tag == ArrayLit   ? static_cast<const ast::ArrayLit*>(expr)->elements
                      : expr->tag == ListLit ? static_cast<const ast::ListLit*>(expr)->elements
                                             : static_cast<const ast::TupleLit*>(expr)->members;
      Run made;
      for (auto one : elements)
        made.push_back(this->expr(one));
      return Value::run(std::move(made));
    }
    case Allocated: {
      // What is in the image is what it says, before the program runs as much
      // as after.
      auto allocated = static_cast<const ast::Allocated*>(expr);
      if (in_the_image(allocated->arena))
        return this->expr(allocated->value);
      break;
    }
    case SetLit: {
      auto set = static_cast<const ast::SetLit*>(expr);
      if (set->arena != nullptr && ! in_the_image(set->arena))
        break;
      auto found = std::make_shared<Table>(set->elements.empty() ? std::nullopt : std::optional<bool>(true));
      for (auto one : set->elements) {
        auto key = value(one);
        hashable(key, one->span);
        found->put(std::move(key), true);
      }
      return found;
    }
    case DictLit: {
      auto dict = static_cast<const ast::DictLit*>(expr);
      if (dict->arena != nullptr && ! in_the_image(dict->arena))
        break;
      auto made = std::make_shared<Table>(false);
      for (const auto& [key, held] : dict->entries) {
        // What is held first, then the key, as Python works the two out.
        auto v = value(held);
        auto k = value(key);
        hashable(k, key->span);
        made->put(std::move(k), std::move(v));
      }
      return made;
    }
    case AddressOf:
      return address_of(static_cast<const ast::AddressOf*>(expr));
    case Deref:
      return deref(static_cast<const ast::Deref*>(expr));
    case Named:
      return this->expr(static_cast<const ast::Named*>(expr)->value);
    case Scoped:
      return block(static_cast<const ast::Scoped*>(expr)->body);
    case If:
      return if_(static_cast<const ast::If*>(expr));
    case While:
      return while_(static_cast<const ast::While*>(expr));
    case ForEach:
      return foreach(static_cast<const ast::ForEach*>(expr));
    default:
      break;
    }
    throw refuse(expr->span, expr);
  }


  // Whether `name` is a local or a variable of the program.
  bool Evaluator::bound(std::string_view name) const
  {
    for (const auto& scope : scopes)
      if (scope.contains(name))
        return true;
    return globals.contains(name) || modules.contains(name);
  }


  // Where the value of `name` lives: a local, then a variable of the program,
  // worked out on first ask and once.
  std::shared_ptr<Cell> Evaluator::cell_of(std::string_view name, Span span)
  {
    for (auto scope = scopes.rbegin(); scope != scopes.rend(); ++scope)
      if (auto found = scope->find(name); found != scope->end())
        return found->second;
    if (auto found = settled.find(name); found != settled.end())
      return found->second;
    if (name == environ_name) {
      auto table = std::make_shared<Table>(std::nullopt, true);
      for (const auto& [key, held] : environ)
        table->put(key, held);
      auto cell = std::make_shared<Cell>(Value(std::move(table)));
      settled[environ_name] = cell;
      return cell;
    }
    if (auto defined = globals.find(name); defined != globals.end()) {
      auto cell = std::make_shared<Cell>();
      settled[name] = cell;
      cell->value = expr(defined->second->value);
      return cell;
    }
    throw CannotEvaluate(span, std::format("'{}' is not something the compiler can work out here", name));
  }


  // What an operator comes to, over what its two sides came to.
  Value Evaluator::binary(const ast::Binary* expr)
  {
    using enum ast::BinaryOp;
    auto op = expr->op;
    // The two that do not work both sides out.
    if (op == short_and || op == logic_and)
      return truth(expr->left) && truth(expr->right);
    if (op == short_or || op == logic_or)
      return truth(expr->left) || truth(expr->right);
    if (op == or_else) {
      // What is on the right is worked out only where the left found nothing.
      Value found;
      try {
        found = value(expr->left, true);
      } catch (const NoAnswer&) {
        found = Missing{};
      }
      return found.is_missing() ? value(expr->right) : found;
    }
    auto left = value(expr->left);
    auto right = value(expr->right);
    try {
      return apply(op, left, right);
    } catch (const NotForThis&) {
      throw CannotEvaluate(expr->span, std::format("'{}' over these", describe(op)));
    } catch (const DivisionByNought&) {
      throw NoAnswer(expr->span, "a division by nought");
    } catch (const TooLarge&) {
      throw CannotEvaluate(expr->span, std::string(too_large_detail));
    }
  }


  // `n²`: a number raised to a power written down.
  Value Evaluator::raised(const ast::Raised* expr)
  {
    auto found = value(expr->base);
    if (! found.is_number()) [[unlikely]]
      throw CannotEvaluate(expr->span, "a power of something that is not a number");
    if (expr->exponent.too_large) [[unlikely]]
      throw CannotEvaluate(expr->span, std::string(too_large_detail));
    try {
      if (auto d = found.get<double>())
        return power(*d, expr->exponent.value);
      auto answer = power(*found.get<__int128>(), expr->exponent.value);
      if (answer.too_large) [[unlikely]]
        throw CannotEvaluate(expr->span, std::string(too_large_detail));
      return answer.value;
    } catch (const DivisionByNought&) {
      throw NoAnswer(expr->span, "a division by nought");
    }
  }


  // What a one-sided operator comes to.
  Value Evaluator::unary(const ast::Unary* expr)
  {
    auto found = value(expr->operand);
    switch (expr->op) {
    case ast::UnaryOp::logic_not:
      if (auto b = found.get<bool>())
        return ! *b;
      break;
    case ast::UnaryOp::length:
      if (auto run = found.get<std::shared_ptr<Run>>())
        return static_cast<__int128>((*run)->size());
      if (auto text = found.get<std::string>())
        return static_cast<__int128>(unicode::length(*text));
      if (auto table = found.get<std::shared_ptr<Table>>())
        return static_cast<__int128>((*table)->size());
      break;
    case ast::UnaryOp::bit_not:
      if (auto n = found.get<__int128>())
        return ~*n;
      break;
    default:
      break;
    }
    throw CannotEvaluate(expr->span, std::format("'{}' over this", describe(expr->op)));
  }


  // A call: to something the compiler provides, or to the program's own.
  Value Evaluator::call_expr(const ast::Call* expr)
  {
    auto callee = ast::as<ast::NameRef>(expr->callee);
    if (callee != nullptr && callee->name == fields_name && expr->args.size() == 1) {
      // What the program's types are is the checker's to say.
      auto found = reflect->fields(expr->args[0]);
      if (! found.has_value()) [[unlikely]]
        throw CannotEvaluate(expr->span, "'\N{APL FUNCTIONAL SYMBOL QUAD}fields' of this");
      return std::move(*found);
    }
    std::vector<Value> arguments;
    for (auto one : expr->args)
      arguments.push_back(this->expr(one));
    if (auto member = ast::as<ast::Member>(expr->callee))
      if (auto base = ast::as<ast::NameRef>(member->base)) {
        const auto& found = builtin(base->name, member->name, expr->span);
        return found.run(expr->span, arguments);
      }
    if (callee != nullptr) {
      auto func = function(callee->name);
      if (func == nullptr) [[unlikely]]
        throw CannotEvaluate(expr->span, std::format("'{}' is not a function of this program", callee->name));
      return call_at(*func, arguments, expr->span);
    }
    throw CannotEvaluate(expr->span, "a call to something worked out");
  }


  // The compiler-provided function `module.name`, where there is one.
  const Builtin& Evaluator::builtin(std::string_view module, std::string_view name, Span span) const
  {
    auto source = modules.find(module);
    auto provided = builtins.find(std::string(source == modules.end() ? std::string_view() : source->second));
    if (provided != builtins.end())
      if (auto found = provided->second.find(std::string(name)); found != provided->second.end())
        return found->second;
    throw CannotEvaluate(span, std::format("'{}.{}' is not something the compiler provides here", module, name));
  }


  // A field of a record, read through a name or through a reference -- or a
  // value of an enumeration, which the checker names.
  Value Evaluator::member(const ast::Member* expr)
  {
    if (auto base = ast::as<ast::NameRef>(expr->base); base != nullptr && ! bound(base->name))
      if (auto named = reflect->enum_value(expr))
        return std::move(*named);
    auto held = record_of(expr->base);
    auto found = held->field(expr->name);
    if (found == nullptr) [[unlikely]]
      throw CannotEvaluate(expr->span, std::format("'{}' has nothing called '{}'", held->type_name, expr->name));
    return *found;
  }


  // One element of an array.
  Value Evaluator::element(const ast::Element* expr)
  {
    auto held = value(expr->base);
    if (expr->indices.size() == 1) {
      if (auto run = held.get<std::shared_ptr<Run>>()) {
        auto keep = *run;
        return (*keep)[as_index(expr->indices[0], keep->size())];
      }
      if (auto text = held.get<std::string>()) {
        std::string keep = *text;
        return character_at(keep, as_index(expr->indices[0], unicode::length(keep)));
      }
    }
    throw CannotEvaluate(expr->span, "reading an element of this");
  }


  // One entry of a collection: what the key stands for, or nothing.
  Value Evaluator::index(const ast::Index* expr)
  {
    auto held = value(expr->base);
    auto key = value(expr->key);
    hashable(key, expr->key->span);
    if (auto table = held.get<std::shared_ptr<Table>>()) {
      if ((*table)->is_set == true && ! (*table)->plain)
        return (*table)->contains(key);
      auto found = (*table)->find(key);
      return found != nullptr ? *found : Value(Missing{});
    }
    throw CannotEvaluate(expr->span, "reading an entry of this");
  }


  // `&x` and `&mut x`, which answer with where the value lives.
  Value Evaluator::address_of(const ast::AddressOf* expr)
  {
    if (auto name = ast::as<ast::NameRef>(expr->operand))
      return Reference{ cell_of(name->name, expr->span), expr->mutable_ };
    throw CannotEvaluate(expr->span, "a reference to this");
  }


  // `r⌖`, which answers with what the reference points at.
  Value Evaluator::deref(const ast::Deref* expr)
  {
    auto found = this->expr(expr->operand);
    auto ref = found.get<Reference>();
    if (ref == nullptr) [[unlikely]]
      throw CannotEvaluate(expr->span, "reading through something that is not a reference");
    return ref->cell->value;
  }


  // -- odds and ends ----------------------------------------------------------

  // What an expression comes to, with a reference followed; a lookup that
  // found nothing is refused unless the caller is `??`.
  Value Evaluator::value(const ast::Expr* expr, bool missing)
  {
    auto found = this->expr(expr);
    if (auto ref = found.get<Reference>()) {
      Value inner = ref->cell->value;
      found = std::move(inner);
    }
    if (found.is_missing() && ! missing) [[unlikely]]
      throw CannotEvaluate(expr->span, "a value that is not there, which \N{DOUBLE QUESTION MARK} reads and nothing "
                                       "else does");
    return found;
  }


  // The record an expression names, however it names it.
  std::shared_ptr<Record> Evaluator::record_of(const ast::Expr* expr)
  {
    auto found = value(expr);
    if (auto record = found.get<std::shared_ptr<Record>>())
      return *record;
    throw CannotEvaluate(expr->span, "this is not a record");
  }


  // What a condition comes to, which has to be a truth value.
  bool Evaluator::truth(const ast::Expr* expr)
  {
    auto found = value(expr);
    if (auto b = found.get<bool>())
      return *b;
    throw CannotEvaluate(expr->span, "a condition that is not a truth value");
  }


  // What a place wanting a whole number comes to.
  __int128 Evaluator::number(const ast::Expr* expr)
  {
    auto found = value(expr);
    if (auto n = found.get<__int128>())
      return *n;
    throw CannotEvaluate(expr->span, "this is not a whole number");
  }


  // An index into something `length` long, checked against it.
  std::size_t Evaluator::as_index(const ast::Expr* expr, std::size_t length)
  {
    auto index = number(expr);
    if (index < 0 || index >= static_cast<__int128>(length)) [[unlikely]]
      throw CannotEvaluate(expr->span, std::format("the index {} is outside a run of {}", ir::decimal(index), length));
    return static_cast<std::size_t>(index);
  }


  // Count one step, and give up where there have been too many.
  void Evaluator::step(Span span)
  {
    if (++steps > steps_allowed) [[unlikely]]
      throw CannotEvaluate(span, std::format("it was still going after {} steps", steps_allowed), true);
  }


  // The refusal for a piece of the language nothing here works out.
  CannotEvaluate Evaluator::refuse(Span span, const ast::Node* node) const
  {
    return CannotEvaluate(span, described(node));
  }

} // namespace pl4g::comptime
