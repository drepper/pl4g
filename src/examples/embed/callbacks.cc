// An example of a C++ program and an interpreted PL4G program calling each
// other's functions through their addresses.
//
// The host hands the program, as numbers, the addresses of functions of its
// own, and the program makes values of the system's convention of them with
// `⎕at(ADDRESS, ⌜@[cdecl] fn(…) → T⌝)` and calls them -- which the
// interpreter does through libffi, as it calls a function the host defines,
// for code it did not make.  One takes twenty arguments, so that some of them
// go where the system's convention puts what its registers do not hold.  And
// the other way: the program hands the host `⎕address` of a `@[cdecl]`
// function of its own, and the host calls it through a pointer of its own --
// the address being that of a libffi closure, which runs the function in the
// interpreter, as often as the host calls it, from within a call the program
// made.
//
// The exit status is nought where every call reached what it should have and
// came back with what it should have, and says what did not otherwise.
//
// Run as `pl4g-embed-callbacks [LIBPL4G-EMBED.SO]`.
#include <cstdint>
#include <exception>
#include <print>
#include <string_view>

#include "embed/pl4g-embed.hh"


namespace {

  // What the program's calls left behind here.
  int combined = 0;
  int applied = 0;

  // A function of mixed arguments, one of them narrow: what the program
  // reaches through the address `combine_at` answers.
  double combine(std::int64_t a, double b, std::int32_t c)
  {
    ++combined;
    return double(a) + b * 2.0 + double(c);
  }

  // Twenty arguments, ten of each kind: more than either kind of register
  // holds on any of the three systems.
  std::int64_t many(std::int64_t a, std::int64_t b, std::int64_t c, std::int64_t d, std::int64_t e, std::int64_t f,
                    std::int64_t g, std::int64_t h, std::int64_t i, std::int64_t j, double x, double y, double z,
                    double w, double p, double q, double r, double s, double t, double u)
  {
    return a + 2 * b + 3 * c + 4 * d + 5 * e + 6 * f + 7 * g + 8 * h + 9 * i + 10 * j
           + std::int64_t(x + y + z + w + p + q + r + s + 100 * t + 1000 * u);
  }

  // Where the two functions above are, as the numbers the program is handed.
  std::uint64_t combine_at()
  {
    return std::uint64_t(reinterpret_cast<std::uintptr_t>(&combine));
  }

  std::uint64_t many_at()
  {
    return std::uint64_t(reinterpret_cast<std::uintptr_t>(&many));
  }

  // Call the function of the program at `at` twice, the second time with
  // what the first answered: code of the program, called by code of the host.
  std::int64_t apply_twice(std::uint64_t at, std::int64_t x)
  {
    ++applied;
    auto f = reinterpret_cast<std::int64_t (*)(std::int64_t)>(static_cast<std::uintptr_t>(at));
    return f(f(x));
  }

  // The program, in PL4G.
  constexpr std::string_view program =
    "let std := \N{APL FUNCTIONAL SYMBOL QUAD}import(\"std\")\n"
    "\n"
    "@[host(\"combine_at\")]\n"
    "fn combine_at() \N{RIGHTWARDS ARROW} u64\n"
    "\n"
    "@[host(\"many_at\")]\n"
    "fn many_at() \N{RIGHTWARDS ARROW} u64\n"
    "\n"
    "@[host(\"apply_twice\")]\n"
    "fn apply_twice(at: u64, x: i64) \N{RIGHTWARDS ARROW} i64\n"
    "\n"
    "@[cdecl]\n"
    "fn tripled(x: i64) \N{RIGHTWARDS ARROW} i64:\n"
    "    x \N{MULTIPLICATION SIGN} 3i64\n"
    "\n"
    "@[startup, impure]\n"
    "fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:\n"
    "    let combine: @[cdecl, impure] fn(i64, f64, i32) \N{RIGHTWARDS ARROW} f64 = \N{APL FUNCTIONAL SYMBOL QUAD}at(\n"
    "        combine_at(), \N{TOP LEFT CORNER}@[cdecl, impure] fn(i64, f64, i32) \N{RIGHTWARDS ARROW} f64"
    "\N{TOP RIGHT CORNER})\n"
    "    let c: f64 = combine(5i64, 0.25f64, \N{SUPERSCRIPT MINUS}3i32)\n"
    "    if c < 2.4f64 \N{LOGICAL OR} c > 2.6f64: return 1u6\n"
    "    let many: @[cdecl] fn(i64, i64, i64, i64, i64, i64, i64, i64, i64, i64,\n"
    "                          f64, f64, f64, f64, f64, f64, f64, f64, f64, f64) \N{RIGHTWARDS ARROW} i64 = "
    "\N{APL FUNCTIONAL SYMBOL QUAD}at(\n"
    "        many_at(), \N{TOP LEFT CORNER}@[cdecl] fn(i64, i64, i64, i64, i64, i64, i64, i64, i64, i64,\n"
    "                                f64, f64, f64, f64, f64, f64, f64, f64, f64, f64) \N{RIGHTWARDS ARROW} i64"
    "\N{TOP RIGHT CORNER})\n"
    "    let got: i64 = many(1i64, 1i64, 1i64, 1i64, 1i64, 1i64, 1i64, 1i64, 1i64, 1i64,\n"
    "                        1f64, 1f64, 1f64, 1f64, 1f64, 1f64, 1f64, 1f64, 2f64, 3f64)\n"
    "    if got \N{NOT EQUAL TO} 55i64 + 8i64 + 200i64 + 3000i64: return 2u6\n"
    "    if apply_twice(\N{APL FUNCTIONAL SYMBOL QUAD}address(tripled), 5i64) \N{NOT EQUAL TO} 45i64: return 3u6\n"
    "    0u6\n";

} // anonymous namespace


int main(int argc, char* argv[])
{
  const char* library = argc > 1 ? argv[1] : pl4g::embed::LoadedInterpreter::default_library;
  try {
    pl4g::embed::LoadedInterpreter interpreter(library);
    if (! interpreter.define("combine_at", combine_at) || ! interpreter.define("many_at", many_at)
        || ! interpreter.define("apply_twice", apply_twice)) [[unlikely]] {
      std::print(stderr, "the interpreter did not keep what it was handed\n");
      return 3;
    }
    if (int status = interpreter.run(program, "callbacks.pl4g"); status != 0) [[unlikely]] {
      std::print(stderr, "the program ended with status {}\n", status);
      return 4;
    }
    if (combined != 1 || applied != 1) [[unlikely]] {
      std::print(stderr, "combine was called {} times and apply_twice {}, once each was due\n", combined, applied);
      return 5;
    }
  } catch (const std::exception& error) {
    std::print(stderr, "{}\n", error.what());
    return 2;
  }
  std::print("the program called two functions of the host through their addresses, and the host one of the "
             "program's through its own\n");
  return 0;
}
