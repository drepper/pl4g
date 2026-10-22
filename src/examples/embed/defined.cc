// An example of a C++ program handing PL4G its functions by name.
//
// The other examples export what a PL4G program calls -- C's linkage, and the
// program linked with -rdynamic -- so that the interpreter finds it with
// dlsym.  This one exports nothing: the functions are of internal linkage and
// the program is linked as any other.  It hands them over instead, with
// pl4g::embed::LoadedInterpreter::define, a name for each, and the
// interpreter asks what it was handed before it asks dlsym.  That is also how
// a host puts a function of its own where the system has one of that name:
// the program's `abs` is this one's, which counts its calls, and not the C
// library's -- and a name the host does not hand over, `labs`, is still found
// with dlsym, in the C library.  Last, a run names a symbol nothing defines and
// is stopped with 92, which ends that run and nothing more.
//
// The exit status is nought where every call reached what it should have, and
// says what did not otherwise.
//
// Run as `pl4g-embed-defined [LIBPL4G-EMBED.SO]`.
#include <cstdint>
#include <cstdlib>
#include <exception>
#include <print>
#include <string>
#include <string_view>
#include <vector>

#include "embed/pl4g-embed.hh"


namespace {

  // What the program's calls left behind here.
  std::int64_t total = 0;
  int abs_calls = 0;
  std::vector<std::string> recorded;

  // Add `n` to the running total, and answer the total.
  std::int64_t tally(std::int64_t n)
  {
    total += n;
    return total;
  }

  // The magnitude of `n`, as the C library's `abs` answers it, counted.
  int counted_abs(int n)
  {
    ++abs_calls;
    return n < 0 ? -n : n;
  }

  // The program, in PL4G.  Nothing in it says where the functions come from:
  // `@[host]` names a symbol, and what it is bound to is the host's business.
  constexpr std::string_view program =
    "let std := \N{APL FUNCTIONAL SYMBOL QUAD}import(\"std\")\n"
    "\n"
    "@[host(\"tally\")]\n"
    "fn tally(n: i64) \N{RIGHTWARDS ARROW} i64\n"
    "\n"
    "@[host(\"abs\")]\n"
    "fn abs(n: i32) \N{RIGHTWARDS ARROW} i32\n"
    "\n"
    "@[host(\"labs\")]\n"
    "fn labs(n: i64) \N{RIGHTWARDS ARROW} i64\n"
    "\n"
    "@[host(\"record\")]\n"
    "fn record(line: str)\n"
    "\n"
    "@[startup, impure]\n"
    "fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:\n"
    "    let sum: mut i64 = 0i64\n"
    "    foreach n := 1i64\N{HORIZONTAL ELLIPSIS}11i64:\n"
    "        sum \N{LEFTWARDS ARROW} tally(n)\n"
    "    record(\"counted to ten\")\n"
    "    let a: i32 = abs(\N{SUPERSCRIPT MINUS}42i32)\n"
    "    let b: i64 = labs(\N{SUPERSCRIPT MINUS}7i64)\n"
    "    record(\"asked for two magnitudes\")\n"
    "    if sum = 55i64 \N{LOGICAL AND} a = 42i32 \N{LOGICAL AND} b = 7i64: 0u6 else: 1u6\n";

  // A program naming a symbol neither the host nor the system defines.
  constexpr std::string_view lost =
    "let std := \N{APL FUNCTIONAL SYMBOL QUAD}import(\"std\")\n"
    "\n"
    "@[host(\"nothing_defines_this\")]\n"
    "fn nowhere() \N{RIGHTWARDS ARROW} i32\n"
    "\n"
    "@[startup, impure]\n"
    "fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:\n"
    "    if nowhere() = 0i32: 0u6 else: 1u6\n";

} // anonymous namespace


int main(int argc, char* argv[])
{
  const char* library = argc > 1 ? argv[1] : pl4g::embed::LoadedInterpreter::default_library;
  try {
    pl4g::embed::LoadedInterpreter interpreter(library);
    // Two functions only this table knows, and one put where the C library
    // has one of the same name.  A function without captures is a function
    // pointer, which is all the table holds.
    if (! interpreter.define("tally", tally) || ! interpreter.define("abs", counted_abs)
        || ! interpreter.define("record", +[](const char* line) { recorded.emplace_back(line); })) [[unlikely]] {
      std::print(stderr, "the interpreter did not keep what it was handed\n");
      return 3;
    }
    if (int status = interpreter.run(program, "defined.pl4g"); status != 0) [[unlikely]] {
      std::print(stderr, "the program ended with status {}\n", status);
      return 4;
    }
    if (total != 55 || abs_calls != 1) [[unlikely]] {
      std::print(stderr, "the total is {} and abs was called here {} times; 55 and once were due\n", total,
                 abs_calls);
      return 5;
    }
    if (recorded != std::vector<std::string>{ "counted to ten", "asked for two magnitudes" }) [[unlikely]] {
      std::print(stderr, "{} lines were recorded, not the two due\n", recorded.size());
      return 6;
    }
    // A symbol nobody defines stops that run, with its own status, and this
    // program goes on.
    if (int status = interpreter.run(lost, "lost.pl4g"); status != 92) [[unlikely]] {
      std::print(stderr, "a symbol nothing defines ended the run with status {}, not 92\n", status);
      return 7;
    }
  } catch (const std::exception& error) {
    std::print(stderr, "{}\n", error.what());
    return 2;
  }
  std::print("tally {}, abs called here once, labs found in the C library, {} lines recorded\n", total,
             recorded.size());
  return 0;
}
