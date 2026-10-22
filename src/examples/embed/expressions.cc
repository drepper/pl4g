// An example of a C++ program handing PL4G work as JSON and taking JSON back.
//
// Arithmetic expressions are written as trees in JSON -- a number is a leaf,
// and an operation is an object naming it and holding its operands,
// `{"op": "+", "args": [1, {"op": "neg", "args": [2]}]}` -- and a PL4G
// program, held in a string and run by the interpreter loaded as a shared
// object (pl4g::embed::LoadedInterpreter), evaluates them.  It asks this
// program for each expression's text through one callback, decodes it with
// the `json` module, works out its value, and hands back what it found as
// JSON through another: `{"id": 3, "value": 2.5}`, or `{"id": 4, "error":
// "division by zero"}`.  The callback only stores the reply.
//
// The expressions are made here with nlohmann::json, a few written out and
// many more grown at random to depths from one to fourteen, and the value each
// must have is worked out while it is grown.  Once the program has run, every
// reply is read with nlohmann::json, checked against a JSON Schema with
// json-schema-validator, which works on what nlohmann::json read, and compared
// with what it should say.  The exit status is nought where every reply was
// well-formed and right, and says what was wrong otherwise.
//
// The callbacks are found by the interpreter with dlsym(RTLD_DEFAULT, ...), so
// they have C's linkage and this program is linked with -rdynamic.
//
// Run as `pl4g-embed-expressions [LIBPL4G-EMBED.SO]`.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <exception>
#include <memory>
#include <optional>
#include <print>
#include <random>
#include <string>
#include <string_view>
#include <vector>

#include <nlohmann/json-schema.hpp>
#include <nlohmann/json.hpp>

#include "embed/pl4g-embed.hh"


namespace {

  // An expression to hand over, and what its reply has to say: a value, or
  // the text of an error.
  struct Case {
    std::string text;
    std::optional<double> value;
    std::string error;
  };

  std::vector<Case> cases;
  // What the program answered for each case, in the order of the cases, and
  // whether it answered at all.
  std::vector<std::optional<std::string>> replies;

  // The program, in PL4G.  `count`, `text_of` and `length_of` are how it asks
  // for the expressions -- the text is read where it is, as a view of the
  // bytes this program holds -- and `answer` is where it hands each reply.
  constexpr std::string_view program =
    "unit \N{CURRENCY SIGN}size \N{RIGHTWARDS ARROW} \N{CURRENCY SIGN}idx\n"
    "\n"
    "let std := \N{APL FUNCTIONAL SYMBOL QUAD}import(\"std\")\n"
    "let json := \N{APL FUNCTIONAL SYMBOL QUAD}import(\"json\")\n"
    "\n"
    "@[host(\"expression_count\")]\n"
    "fn count() \N{RIGHTWARDS ARROW} u64\n"
    "\n"
    "@[host(\"expression_text\")]\n"
    "fn text_of(n: u64) \N{RIGHTWARDS ARROW} i64\n"
    "\n"
    "@[host(\"expression_length\")]\n"
    "fn length_of(n: u64) \N{RIGHTWARDS ARROW} u64\n"
    "\n"
    "@[host(\"expression_answer\")]\n"
    "fn answer(n: u64, reply: str)\n"
    "\n"
    "enum Trouble:\n"
    "    malformed ;\n"
    "    division_by_zero\n"
    "\n"
    "fn reason(t: Trouble) \N{RIGHTWARDS ARROW} str:\n"
    "    if t = Trouble.division_by_zero: \"division by zero\" else: \"malformed expression\"\n"
    "\n"
    "fn field(members: [json.Member], name: str) \N{RIGHTWARDS ARROW} json.Value:\n"
    "    let i: mut u64 \N{CURRENCY SIGN}idx = 0\n"
    "    let n: u64 \N{CURRENCY SIGN}idx = #members\n"
    "    while i < n:\n"
    "        if members\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}i\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}.key = name:\n"
    "            return members\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}i\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}.value\n"
    "        i \N{LEFTWARDS ARROW} i + 1\n"
    "    json.Value(.null \N{LEFTWARDS ARROW} true)\n"
    "\n"
    "fn number(v: json.Value) \N{RIGHTWARDS ARROW} f64 ? Trouble:\n"
    "    match v:\n"
    "        i64(k): \N{APL FUNCTIONAL SYMBOL QUAD}float(k, \N{TOP LEFT CORNER}f64\N{TOP RIGHT CORNER})\n"
    "        u64(k): \N{APL FUNCTIONAL SYMBOL QUAD}float(k, \N{TOP LEFT CORNER}f64\N{TOP RIGHT CORNER})\n"
    "        f64(x): x\n"
    "        [json.Member](members): applied(members)\n"
    "        _: \N{UP TACK} Trouble.malformed\n"
    "\n"
    "fn applied(members: [json.Member]) \N{RIGHTWARDS ARROW} f64 ? Trouble:\n"
    "    match field(members, \"args\"):\n"
    "        [json.Value](args):\n"
    "            match field(members, \"op\"):\n"
    "                str(op): combined(op, args)\n"
    "                _: \N{UP TACK} Trouble.malformed\n"
    "        _: \N{UP TACK} Trouble.malformed\n"
    "\n"
    "fn combined(op: str, args: [json.Value]) \N{RIGHTWARDS ARROW} f64 ? Trouble:\n"
    "    let n: u64 \N{CURRENCY SIGN}idx = #args\n"
    "    if n = 0:\n"
    "        return \N{UP TACK} Trouble.malformed\n"
    "    let first: f64 = number(args\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}0\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET})?\n"
    "    if op = \"neg\" \N{LOGICAL OR} op = \"abs\":\n"
    "        if n \N{NOT EQUAL TO} 1:\n"
    "            return \N{UP TACK} Trouble.malformed\n"
    "        if op = \"neg\" \N{LOGICAL OR} first < 0f64:\n"
    "            return 0f64 - first\n"
    "        return first\n"
    "    let acc: mut f64 = first\n"
    "    let i: mut u64 \N{CURRENCY SIGN}idx = 1\n"
    "    while i < n:\n"
    "        let x: f64 = number(args\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}i\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET})?\n"
    "        if op = \"+\":\n"
    "            acc \N{LEFTWARDS ARROW} acc + x\n"
    "        elif op = \"-\":\n"
    "            acc \N{LEFTWARDS ARROW} acc - x\n"
    "        elif op = \"*\":\n"
    "            acc \N{LEFTWARDS ARROW} acc \N{MULTIPLICATION SIGN} x\n"
    "        elif op = \"/\":\n"
    "            match acc \N{DIVISION SIGN} x:\n"
    "                f64(q): acc \N{LEFTWARDS ARROW} q\n"
    "                \N{UP TACK}: return \N{UP TACK} Trouble.division_by_zero\n"
    "        elif op = \"min\":\n"
    "            acc \N{LEFTWARDS ARROW} acc \N{LEFT FLOOR} x\n"
    "        elif op = \"max\":\n"
    "            acc \N{LEFTWARDS ARROW} acc \N{LEFT CEILING} x\n"
    "        else:\n"
    "            return \N{UP TACK} Trouble.malformed\n"
    "        i \N{LEFTWARDS ARROW} i + 1\n"
    "    acc\n"
    "\n"
    "fn reply_to(id: u64, value: f64 ? Trouble, a: &mut arena) \N{RIGHTWARDS ARROW} json.Value in a:\n"
    "    let key: json.Member = json.Member(.key \N{LEFTWARDS ARROW} \"id\", .value \N{LEFTWARDS ARROW} "
    "json.Value(.integer \N{LEFTWARDS ARROW} \N{APL FUNCTIONAL SYMBOL QUAD}narrow(id, \N{TOP LEFT CORNER}i64\N{TOP RIGHT CORNER}) ?? 0i64))\n"
    "    match value:\n"
    "        f64(x):\n"
    "            json.Value(.object \N{LEFTWARDS ARROW} [key, json.Member(.key \N{LEFTWARDS ARROW} \"value\", "
    ".value \N{LEFTWARDS ARROW} json.Value(.number \N{LEFTWARDS ARROW} x))] in a)\n"
    "        \N{UP TACK}(t):\n"
    "            json.Value(.object \N{LEFTWARDS ARROW} [key, json.Member(.key \N{LEFTWARDS ARROW} \"error\", "
    ".value \N{LEFTWARDS ARROW} json.Value(.text \N{LEFTWARDS ARROW} reason(t)))] in a)\n"
    "\n"
    "fn evaluated(bytes: u8\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}) "
    "\N{RIGHTWARDS ARROW} f64 ? Trouble:\n"
    "    let pool: mut arena = \N{APL FUNCTIONAL SYMBOL QUAD}arena\n"
    "    match json.decode(bytes, &mut pool):\n"
    "        json.Value(v): number(v)\n"
    "        \N{UP TACK}(_): \N{UP TACK} Trouble.malformed\n"
    "\n"
    "@[startup, impure]\n"
    "fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:\n"
    "    let total: u64 = count()\n"
    "    let n: mut u64 = 0u64\n"
    "    while n < total:\n"
    "        let pool: mut arena = \N{APL FUNCTIONAL SYMBOL QUAD}arena\n"
    "        let bytes: u8\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} = "
    "\N{APL FUNCTIONAL SYMBOL QUAD}span(text_of(n), length_of(n), \N{TOP LEFT CORNER}u8"
    "\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}\N{TOP RIGHT CORNER})\n"
    "        let outcome: f64 ? Trouble = evaluated(bytes)\n"
    "        let text: [u8] = json.encode(reply_to(n, outcome, &mut pool), &mut pool) ?? []\n"
    "        answer(n, \N{APL FUNCTIONAL SYMBOL QUAD}text(text) ?? \"\")\n"
    "        n \N{LEFTWARDS ARROW} n + 1u64\n"
    "    0u6\n";

  // What a reply has to look like: the expression's number, and either the
  // value it came to or why it came to none -- one of the two, and nothing
  // else.
  constexpr std::string_view reply_schema = R"({
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
      "id": { "type": "integer", "minimum": 0 },
      "value": { "type": "number" },
      "error": { "type": "string", "enum": [ "division by zero", "malformed expression" ] }
    },
    "required": [ "id" ],
    "oneOf": [ { "required": [ "value" ] }, { "required": [ "error" ] } ],
    "additionalProperties": false
  })";

  // `value` written as JSON, compactly, the way the PL4G program reads it.
  std::string written(const ::nlohmann::json& value)
  {
    return value.dump();
  }

  // An operation of `op` over `args`, as the expressions write one.
  ::nlohmann::json operation(const char* op, std::vector<::nlohmann::json> args)
  {
    return { { "op", op }, { "args", std::move(args) } };
  }



  // Expressions grown at random, each with the value it has, worked out as the
  // PL4G program works it out: an operation folds its operands from the left.
  class Grower {
  public:
    explicit Grower(std::uint32_t seed) : random(seed) {}

    // An expression of exactly `depth` levels, and its value.  An operation
    // whose value would leave the range kept to is grown again with other
    // operands, so that no expression comes to an infinity, which a PL4G
    // program stops at rather than answers.  Division by nought is left
    // out here; the cases written out below hold it.
    std::pair<::nlohmann::json, double> grown(int depth)
    {
      if (depth <= 1)
        return leaf();
      for (;;) {
        static constexpr const char* ops[] = { "+", "-", "*", "/", "min", "max", "neg", "abs" };
        auto op = std::string_view(ops[pick(0, 7)]);
        auto count = op == "neg" || op == "abs" ? 1 : pick(2, 3);
        std::vector<::nlohmann::json> args;
        std::vector<double> values;
        // One operand reaches the whole depth; the others are shallow, so that
        // an expression grows with its depth and not with a power of it.
        auto deep = pick(0, count - 1);
        for (int at = 0; at < count; ++at) {
          auto [arg, value] = grown(at == deep ? depth - 1 : pick(1, std::min(depth - 1, 3)));
          args.push_back(std::move(arg));
          values.push_back(value);
        }
        auto value = folded(op, values);
        if (value && std::isfinite(*value) && std::fabs(*value) < 1e12)
          return { operation(std::string(op).c_str(), std::move(args)), *value };
      }
    }

  private:
    // A number: a whole one, or one with two decimals, never nought, so
    // that no division grown here is one by nought.
    std::pair<::nlohmann::json, double> leaf()
    {
      auto whole = pick(1, 99) * (pick(0, 1) == 0 ? 1 : -1);
      if (pick(0, 2) == 0)
        return { ::nlohmann::json(std::int64_t(whole)), double(whole) };
      auto value = double(whole) + double(pick(1, 99)) / 100.0 * (whole < 0 ? -1.0 : 1.0);
      return { ::nlohmann::json(value), value };
    }

    // The value of `op` over `values`, or nothing where a division is by
    // nought.
    static std::optional<double> folded(std::string_view op, const std::vector<double>& values)
    {
      if (op == "neg")
        return 0.0 - values[0];
      if (op == "abs")
        return values[0] < 0.0 ? 0.0 - values[0] : values[0];
      double acc = values[0];
      for (std::size_t at = 1; at < values.size(); ++at) {
        double x = values[at];
        if (op == "+")
          acc = acc + x;
        else if (op == "-")
          acc = acc - x;
        else if (op == "*")
          acc = acc * x;
        else if (op == "/") {
          if (x == 0.0) [[unlikely]]
            return std::nullopt;
          acc = acc / x;
        } else if (op == "min")
          acc = std::fmin(acc, x);
        else
          acc = std::fmax(acc, x);
      }
      return acc;
    }

    // A number from `low` to `high`, both included.
    int pick(int low, int high) { return std::uniform_int_distribution<int>(low, high)(random); }

    std::mt19937 random;
  };


  // The expressions to hand over: some written out -- among them the two ways
  // an expression comes to no value -- and many more grown at random.
  void make_cases()
  {
    auto add = [](const ::nlohmann::json& expression, std::optional<double> value, std::string error = {}) {
      cases.push_back({ written(expression), value, std::move(error) });
    };
    add(::nlohmann::json(42), 42.0);
    add(operation("+", { 1, 2 }), 3.0);
    add(operation("*", { operation("+", { 1, 2 }), operation("-", { 10, 4 }) }), 18.0);
    add(operation("/", { 7, 2 }), 3.5);
    add(operation("neg", { operation("max", { 3, 9.5, -2 }) }), -9.5);
    add(operation("/", { 1, 0 }), std::nullopt, "division by zero");
    add(operation("+", { 1, operation("/", { operation("-", { 2, 2 }), 0 }) }), std::nullopt, "division by zero");
    add(operation("pow", { 2, 8 }), std::nullopt, "malformed expression");
    add(operation("neg", { 1, 2 }), std::nullopt, "malformed expression");
    add(operation("+", {}), std::nullopt, "malformed expression");
    add(operation("+", { 1, "two" }), std::nullopt, "malformed expression");
    add(::nlohmann::json("seven"), std::nullopt, "malformed expression");
    cases.push_back({ "{\"op\": \"+\", \"args\": [1, 2", std::nullopt, "malformed expression" });
    Grower grower(20261022);
    for (int depth = 1; depth <= 14; ++depth)
      for (int one = 0; one < 8; ++one) {
        auto [expression, value] = grower.grown(depth);
        add(expression, value);
      }
    replies.assign(cases.size(), std::nullopt);
  }


  // What is wrong with the reply to case `id`, or nothing.
  std::optional<std::string> wrong_with(std::size_t id, const ::nlohmann::json_schema::json_validator& validator)
  {
    if (! replies[id]) [[unlikely]]
      return "no reply";
    const auto& text = *replies[id];
    // Read once, without throwing: what was read is what the schema is
    // asked of and what is compared.
    auto reply = ::nlohmann::json::parse(text, nullptr, false);
    if (reply.is_discarded()) [[unlikely]]
      return std::format("{} cannot be read", text);
    try {
      validator.validate(reply);
    } catch (const std::exception& error) {
      return std::format("{} does not fit the schema: {}", text, error.what());
    }
    if (reply["id"].get<std::uint64_t>() != id) [[unlikely]]
      return std::format("{} names the wrong expression", text);
    const auto& due = cases[id];
    if (due.value) {
      if (! reply.contains("value") || reply["value"].get<double>() != *due.value) [[unlikely]]
        return std::format("{} where {} was due", text, *due.value);
    } else if (! reply.contains("error") || reply["error"].get<std::string>() != due.error) [[unlikely]]
      return std::format("{} where the error '{}' was due", text, due.error);
    return std::nullopt;
  }

} // anonymous namespace


// How many expressions there are.
extern "C" std::uint64_t expression_count()
{
  return cases.size();
}


// Where the text of expression `n` is, which the program reads in place.
extern "C" std::int64_t expression_text(std::uint64_t n)
{
  return n < cases.size() ? std::int64_t(reinterpret_cast<std::uintptr_t>(cases[n].text.data())) : 0;
}


// How many bytes the text of expression `n` has.
extern "C" std::uint64_t expression_length(std::uint64_t n)
{
  return n < cases.size() ? cases[n].text.size() : 0;
}


// Where the program hands the reply to expression `n`.  Only stored: main
// reads the replies once the program has run.
extern "C" void expression_answer(std::uint64_t n, const char* reply)
{
  if (n < replies.size())
    replies[n] = reply;
}


int main(int argc, char* argv[])
{
  const char* library = argc > 1 ? argv[1] : pl4g::embed::LoadedInterpreter::default_library;
  make_cases();
  ::nlohmann::json_schema::json_validator validator;
  try {
    validator.set_root_schema(::nlohmann::json::parse(reply_schema));
    pl4g::embed::LoadedInterpreter interpreter(library);
    if (int status = interpreter.run(program, "expressions.pl4g"); status != 0) [[unlikely]] {
      std::print(stderr, "the program ended with status {}\n", status);
      return 4;
    }
  } catch (const std::exception& error) {
    std::print(stderr, "{}\n", error.what());
    return 2;
  }
  int wrong = 0;
  for (std::size_t id = 0; id < cases.size(); ++id)
    if (auto why = wrong_with(id, validator); why) [[unlikely]] {
      std::print(stderr, "expression {}: {}\n", id, *why);
      ++wrong;
    }
  if (wrong > 0) [[unlikely]] {
    std::print(stderr, "{} of {} replies were wrong\n", wrong, cases.size());
    return 6;
  }
  std::print("{} expressions, depths 1 to 14: every reply fits the schema and is right\n", cases.size());
  return 0;
}
