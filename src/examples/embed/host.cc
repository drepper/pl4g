// An example of a C++ program embedding PL4G's interpreter.
//
// The same program as host.c, through embed/pl4g-embed.hh: an object of
// pl4g::embed::LoadedInterpreter loads the interpreter as a shared object,
// finds what it offers and makes a state, and gives all of it back when it is
// destroyed.  The PL4G program, held in a string, asks this one for its input
// through two functions defined here, works out a few things about it, and
// hands the answers back through a third, which only stores them.  Once the
// program has run, main checks what was stored against what it works out
// itself, and exits with 0 where everything agrees and with a number saying
// what did not.  It does so with two interpreters alive at the same time.
//
// The three functions are found by the interpreter with
// dlsym(RTLD_DEFAULT, ...), so they have C's linkage and this program is
// linked with -rdynamic: what it defines is then in its dynamic symbol table,
// where dlsym looks.
//
// Run as `pl4g-embed-example-cc [LIBPL4G-EMBED.SO]`.
#include <algorithm>
#include <array>
#include <cstdint>
#include <exception>
#include <numeric>
#include <print>
#include <string_view>

#include "embed/pl4g-embed.hh"


namespace {

  // The input: each reading, and so how many the program is told there are.
  constexpr std::array<std::int64_t, 10> readings{ 17, -4, 256, 9, 0, 1000, -73, 42, 5, 5 };

  // What the program delivers, and whether it did.
  struct Result {
    bool delivered = false;
    std::int64_t count = 0;
    std::int64_t sum = 0;
    std::int64_t smallest = 0;
    std::int64_t largest = 0;
    std::int64_t squares = 0;

    bool operator==(const Result&) const = default;
  };

  Result result;

  // The program, in PL4G.  It declares the three functions below as defined
  // by its host, reads every reading, and delivers how many there were, their
  // sum, the smallest and the largest, and the sum of their squares.
  constexpr std::string_view program =
    "@[host(\"example_count\")]\n"
    "fn count() \N{RIGHTWARDS ARROW} i64\n"
    "\n"
    "@[host(\"example_value\")]\n"
    "fn value(at: i64) \N{RIGHTWARDS ARROW} i64\n"
    "\n"
    "@[host(\"example_deliver\")]\n"
    "fn deliver(count: i64, sum: i64, smallest: i64, largest: i64, squares: i64)\n"
    "\n"
    "let std := \N{APL FUNCTIONAL SYMBOL QUAD}import(\"std\")\n"
    "\n"
    "@[startup, impure]\n"
    "fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:\n"
    "    let n: i64 = count()\n"
    "    let sum: mut i64 = 0i64\n"
    "    let squares: mut i64 = 0i64\n"
    "    let smallest: mut i64 = value(0i64)\n"
    "    let largest: mut i64 = smallest\n"
    "    foreach at := 0i64\N{HORIZONTAL ELLIPSIS}n:\n"
    "        let x: i64 = value(at)\n"
    "        sum \N{LEFTWARDS ARROW} sum + x\n"
    "        squares \N{LEFTWARDS ARROW} squares + x \N{MULTIPLICATION SIGN} x\n"
    "        if x < smallest:\n"
    "            smallest \N{LEFTWARDS ARROW} x\n"
    "        if x > largest:\n"
    "            largest \N{LEFTWARDS ARROW} x\n"
    "    deliver(n, sum, smallest, largest, squares)\n"
    "    0u6\n";

  // What the answers have to be, worked out here.
  Result due()
  {
    auto [smallest, largest] = std::ranges::minmax(readings);
    return { true,
             std::int64_t(readings.size()),
             std::accumulate(readings.begin(), readings.end(), std::int64_t(0)),
             smallest,
             largest,
             std::accumulate(readings.begin(), readings.end(), std::int64_t(0),
                             [](std::int64_t sum, std::int64_t x) { return sum + x * x; }) };
  }

  // What was delivered, held to what is due: 0 where it agrees.
  int check()
  {
    if (! result.delivered) [[unlikely]] {
      std::print(stderr, "the program delivered nothing\n");
      return 5;
    }
    auto wanted = due();
    if (result != wanted) [[unlikely]] {
      std::print(stderr, "delivered {}, {}, {}, {}, {} where {}, {}, {}, {}, {} were due\n", result.count,
                 result.sum, result.smallest, result.largest, result.squares, wanted.count, wanted.sum,
                 wanted.smallest, wanted.largest, wanted.squares);
      return 6;
    }
    std::print("delivered: {} readings, sum {}, smallest {}, largest {}, sum of squares {}\n", result.count,
               result.sum, result.smallest, result.largest, result.squares);
    return 0;
  }

} // anonymous namespace


// How many readings there are.
extern "C" std::int64_t example_count()
{
  return std::int64_t(readings.size());
}


// The reading at `at`, or nought for an index outside them.
extern "C" std::int64_t example_value(std::int64_t at)
{
  return at >= 0 && std::size_t(at) < readings.size() ? readings[std::size_t(at)] : 0;
}


// Where the program hands its answers.  Only stored: main reads them once the
// program has run.
extern "C" void example_deliver(std::int64_t count, std::int64_t sum, std::int64_t smallest, std::int64_t largest,
                                std::int64_t squares)
{
  result = { true, count, sum, smallest, largest, squares };
}


int main(int argc, char* argv[])
{
  const char* library = argc > 1 ? argv[1] : pl4g::embed::LoadedInterpreter::default_library;
  try {
    // Two at once, each with a state of its own.
    pl4g::embed::LoadedInterpreter first(library), second(library);
    for (auto* interpreter : { &first, &second }) {
      result = {};
      if (int status = interpreter->run(program, "example.pl4g"); status != 0) [[unlikely]] {
        std::print(stderr, "the program ended with status {}\n", status);
        return 4;
      }
      if (int wrong = check(); wrong != 0) [[unlikely]]
        return wrong;
    }
  } catch (const std::exception& error) {
    std::print(stderr, "{}\n", error.what());
    return 2;
  }
  return 0;
}
