// A program that embeds the interpreter and defines what the programs it runs
// call: a test of pl4g::embed and of the interpreter's host calls.
//
// Each function below is `extern "C"`, and the program is linked with its
// symbols exported, which is what an embedding program has to do for `dlsym`
// to find them.  Each records what it was handed and answers something of its
// own; the PL4G programs, given as text in memory, compare what came back
// with what it should be, and say how each comparison went through
// `embed_verdict`.  So a value that crosses wrongly is caught on whichever
// side it arrives at: here, where an argument is recorded, and there, where
// an answer is compared -- with the interpreter's own view of the value, which
// is what a wrong extension of a narrow integer would spoil.
//
// Exits with 0 where everything went as it should, saying what did not on
// standard error otherwise.
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <print>
#include <string>
#include <thread>
#include <tuple>
#include <string_view>
#include <vector>

#include <sys/mman.h>
#include <unistd.h>

#include "embed/embed.hh"


namespace {

  // What the host functions were handed, and what the programs said.
  struct Seen {
    std::int8_t i8 = 0;
    std::int16_t i16 = 0;
    std::int32_t i32 = 0;
    std::int64_t i64 = 0;
    std::uint8_t u8 = 0;
    std::uint16_t u16 = 0;
    std::uint32_t u32 = 0;
    std::uint64_t u64 = 0;
    bool truth = true;
    std::uint32_t character = 0;
    float single = 0;
    double twice = 0;
    std::string first;
    std::string second;
    unsigned calls = 0;
    std::vector<std::uint32_t> verdicts;
    std::vector<std::uint32_t> failed;
  };

  Seen seen;

  // How many things went wrong.
  unsigned wrong = 0;

  // Say that `what` is not as it should be, if `ok` says it is not.
  void expect(bool ok, std::string_view what)
  {
    if (! ok) [[unlikely]] {
      std::println(stderr, "embed-host: {}", what);
      ++wrong;
    }
  }

} // anonymous namespace


// What the PL4G programs call.  Each integer comes back moved from what it
// was by something that reaches its top bit, or its sign, or both.
extern "C" {

  // Record `v` and answer one less.
  std::int8_t embed_i8(std::int8_t v)
  {
    ++seen.calls;
    seen.i8 = v;
    return std::int8_t(v - 1);
  }

  // Record `v` and answer one less.
  std::int16_t embed_i16(std::int16_t v)
  {
    seen.i16 = v;
    return std::int16_t(v - 1);
  }

  // Record `v` and answer one less.
  std::int32_t embed_i32(std::int32_t v)
  {
    seen.i32 = v;
    return v - 1;
  }

  // Record `v` and answer three times as much.
  std::int64_t embed_i64(std::int64_t v)
  {
    seen.i64 = v;
    return v * 3;
  }

  // Record `v` and answer the largest there is.
  std::uint8_t embed_u8(std::uint8_t v)
  {
    seen.u8 = v;
    return std::uint8_t(v + 5);
  }

  // Record `v` and answer the largest there is.
  std::uint16_t embed_u16(std::uint16_t v)
  {
    seen.u16 = v;
    return std::uint16_t(v + 535);
  }

  // Record `v` and answer the largest there is.
  std::uint32_t embed_u32(std::uint32_t v)
  {
    seen.u32 = v;
    return v + 294967295u;
  }

  // Record `v` and answer one more.
  std::uint64_t embed_u64(std::uint64_t v)
  {
    seen.u64 = v;
    return v + 1;
  }

  // Record `v` and answer the other truth value.
  bool embed_bool(bool v)
  {
    seen.truth = v;
    return ! v;
  }

  // Record `c` and answer the character after it.
  std::uint32_t embed_char(std::uint32_t c)
  {
    seen.character = c;
    return c + 1;
  }

  // Record `v` and answer half of it.
  float embed_f32(float v)
  {
    seen.single = v;
    return v / 2;
  }

  // Record `v` and answer twice it.
  double embed_f64(double v)
  {
    seen.twice = v;
    return v * 2;
  }

  // Add one to what `n` points to.
  void embed_bump(std::int32_t* n)
  {
    ++*n;
  }

  // The record a PL4G program lays out as C does, with `@[abi]`.
  struct Pair {
    std::int64_t first;
    double second;
  };

  // Scale both of the pair's members by `by`; whether the second came to three.
  bool embed_scale(Pair* p, double by)
  {
    p->first = std::int64_t(double(p->first) * by);
    p->second *= by;
    return p->second == 3.0;
  }

  // Records answered by value, as C answers them: sixteen bytes in two
  // registers of two kinds, twelve of three `float`s, thirty-two through room
  // the caller made, and six of an array and a number.
  Pair embed_pair(std::int64_t n, double x)
  {
    return { n + 1, x * 2 };
  }

  struct Floats {
    float x, y, z;
  };

  Floats embed_floats(float x)
  {
    return { x, x + 1, x + 2 };
  }

  struct Quad {
    std::int64_t a;
    double b;
    std::int32_t c;
    double d;
  };

  Quad embed_quad(std::int64_t n)
  {
    return { n, 0.5, std::int32_t(n) - 1, 0.25 };
  }

  struct Bytes {
    std::uint8_t v[3];
    std::uint16_t tag;
  };

  Bytes embed_bytes(std::uint8_t n)
  {
    return { { n, std::uint8_t(n + 1), std::uint8_t(n + 2) }, 513 };
  }

  // Record the two texts, which have to end in a nul; their lengths together.
  std::uint64_t embed_words(const char* a, const char* b)
  {
    seen.first = a;
    seen.second = b;
    return std::strlen(a) + std::strlen(b);
  }

  // More arguments than there are registers for, of every kind, in an order
  // that mixes the integer registers with the floating-point ones.
  double embed_many(std::uint8_t a, std::int16_t b, float c, std::int64_t d, double e, std::uint32_t f, std::int8_t g,
                    double h, std::uint64_t i, float j, std::int32_t k, std::uint16_t l, double m)
  {
    return double(a) + double(b) + double(c) + double(d) + e + double(f) + double(g) + h + double(i) + double(j)
           + double(k) + double(l) + m;
  }

  // What a program says of the comparison numbered `which`.
  void embed_verdict(std::uint32_t which, bool ok)
  {
    seen.verdicts.push_back(which);
    if (! ok)
      seen.failed.push_back(which);
  }

  // What a host function declared to answer `str` answers: the bytes, which
  // the host keeps, and how many there are.
  struct Text {
    const char* text;
    std::uint64_t length;
  };

  Text embed_greeting()
  {
    return { "hello, world", 12 };
  }

  // A nul inside, and a character of three bytes after it.
  Text embed_with_nul()
  {
    return { "a\0b\xe2\x82\xac", 6 };
  }

  Text embed_empty()
  {
    return { "", 0 };
  }

  // No text at all, the length beside it not looked at.
  Text embed_nothing()
  {
    return { nullptr, 7 };
  }

  // `s` and an exclamation mark, out of a buffer kept until the next call:
  // the program has copied the bytes by then.
  Text embed_echo(const char* s)
  {
    static std::string kept;
    kept = s;
    kept += '!';
    return { kept.data(), kept.size() };
  }

  // `n` bytes of one buffer the host keeps, copied and given back each time.
  Text embed_big(std::uint64_t n)
  {
    static std::string kept;
    if (kept.size() < n)
      kept.assign(n, 'x');
    return { kept.data(), n };
  }

  // A nul written the long way, which is no UTF-8.
  Text embed_overlong()
  {
    return { "a\xc0\x80", 3 };
  }

} // extern "C"


namespace {

  // Every kind of value, there and back.
  constexpr const char* values = "@[abi]\n"
    "type Pair =\n"
    "    first : i64 ; second : f64\n"
    "\n"
    "@[abi]\ntype Floats = x : f32 ; y : f32 ; z : f32\n"
    "@[abi]\ntype Quad = a : i64 ; b : f64 ; c : i32 ; d : f64\n"
    "@[abi]\ntype Bytes = v : u8\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}3\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} ; tag : u16\n"
    "\n"
    "@[host(\"embed_pair\")]\nfn embed_pair(n: i64, x: f64) \N{RIGHTWARDS ARROW} Pair\n"
    "@[host(\"embed_floats\")]\nfn embed_floats(x: f32) \N{RIGHTWARDS ARROW} Floats\n"
    "@[host(\"embed_quad\")]\nfn embed_quad(n: i64) \N{RIGHTWARDS ARROW} Quad\n"
    "@[host(\"embed_bytes\")]\nfn embed_bytes(n: u8) \N{RIGHTWARDS ARROW} Bytes\n"
    "@[host(\"embed_i8\")]\nfn embed_i8(v: i8) \N{RIGHTWARDS ARROW} i8\n"
    "@[host(\"embed_i16\")]\nfn embed_i16(v: i16) \N{RIGHTWARDS ARROW} i16\n"
    "@[host(\"embed_i32\")]\nfn embed_i32(v: i32) \N{RIGHTWARDS ARROW} i32\n"
    "@[host(\"embed_i64\")]\nfn embed_i64(v: i64) \N{RIGHTWARDS ARROW} i64\n"
    "@[host(\"embed_u8\")]\nfn embed_u8(v: u8) \N{RIGHTWARDS ARROW} u8\n"
    "@[host(\"embed_u16\")]\nfn embed_u16(v: u16) \N{RIGHTWARDS ARROW} u16\n"
    "@[host(\"embed_u32\")]\nfn embed_u32(v: u32) \N{RIGHTWARDS ARROW} u32\n"
    "@[host(\"embed_u64\")]\nfn embed_u64(v: u64) \N{RIGHTWARDS ARROW} u64\n"
    "@[host(\"embed_bool\")]\nfn embed_bool(v: bool) \N{RIGHTWARDS ARROW} bool\n"
    "@[host(\"embed_char\")]\nfn embed_char(c: char) \N{RIGHTWARDS ARROW} char\n"
    "@[host(\"embed_f32\")]\nfn embed_f32(v: f32) \N{RIGHTWARDS ARROW} f32\n"
    "@[host(\"embed_f64\")]\nfn embed_f64(v: f64) \N{RIGHTWARDS ARROW} f64\n"
    "@[host(\"embed_bump\")]\nfn embed_bump(n: &mut i32)\n"
    "@[host(\"embed_scale\")]\nfn embed_scale(p: &mut Pair, by: f64) \N{RIGHTWARDS ARROW} bool\n"
    "@[host(\"embed_words\")]\nfn embed_words(a: str, b: str) \N{RIGHTWARDS ARROW} u64\n"
    "@[host(\"embed_many\")]\n"
    "fn embed_many(a: u8, b: i16, c: f32, d: i64, e: f64, f: u32, g: i8, h: f64, i: u64, j: f32, k: i32, l: u16,\n"
    "              m: f64) \N{RIGHTWARDS ARROW} f64\n"
    "@[host(\"embed_verdict\")]\nfn verdict(which: u32, ok: bool)\n"
    "\n"
    "let std := \N{APL FUNCTIONAL SYMBOL QUAD}import(\"std\")\n"
    "\n"
    "@[startup, impure]\n"
    "fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:\n"
    "    verdict(1u32, embed_i8(\N{SUPERSCRIPT MINUS}100i8) = \N{SUPERSCRIPT MINUS}101i8)\n"
    "    verdict(2u32, embed_i16(\N{SUPERSCRIPT MINUS}30000i16) = \N{SUPERSCRIPT MINUS}30001i16)\n"
    "    verdict(3u32, embed_i32(\N{SUPERSCRIPT MINUS}2000000000i32) = \N{SUPERSCRIPT MINUS}2000000001i32)\n"
    "    verdict(4u32, embed_i64(\N{SUPERSCRIPT MINUS}5000000000i64) = \N{SUPERSCRIPT MINUS}15000000000i64)\n"
    "    verdict(5u32, embed_u8(250u8) = 255u8)\n"
    "    verdict(6u32, embed_u16(65000u16) = 65535u16)\n"
    "    verdict(7u32, embed_u32(4000000000u32) = 4294967295u32)\n"
    "    verdict(8u32, embed_u64(18000000000000000000u64) = 18000000000000000001u64)\n"
    "    verdict(9u32, \N{NOT SIGN}embed_bool(true))\n"
    "    verdict(10u32, embed_bool(false))\n"
    "    verdict(11u32, embed_char('a') = 'b')\n"
    "    @[ignore(4217)]\n"
    "    verdict(12u32, embed_f32(3f32) = 1.5f32)\n"
    "    @[ignore(4217)]\n"
    "    verdict(13u32, embed_f64(2.25f64) = 4.5f64)\n"
    "    let k: mut i32 = 41i32\n"
    "    embed_bump(&mut k)\n"
    "    verdict(14u32, k = 42i32)\n"
    "    let p: mut Pair = Pair(.first \N{LEFTWARDS ARROW} 3i64, .second \N{LEFTWARDS ARROW} 1.5f64)\n"
    "    @[ignore(4217)]\n"
    "    verdict(15u32, embed_scale(&mut p, 2f64) \N{LOGICAL AND} p.first = 6i64 \N{LOGICAL AND} p.second = 3f64)\n"
    "    verdict(16u32, embed_words(\"host\", \"functions\") = 13u64)\n"
    "    @[ignore(4217)]\n"
    "    verdict(17u32, embed_many(1u8, \N{SUPERSCRIPT MINUS}2i16, 0.5f32, 4i64, 0.25f64, 6u32, \N{SUPERSCRIPT MINUS}7i8,"
    " 8f64, 9u64, 10f32, \N{SUPERSCRIPT MINUS}11i32, 12u16, 13f64) = 43.75f64)\n"
    "    let pair: Pair = embed_pair(\N{SUPERSCRIPT MINUS}4i64, 1.25f64)\n"
    "    @[ignore(4217)]\n"
    "    verdict(18u32, pair.first = \N{SUPERSCRIPT MINUS}3i64 \N{LOGICAL AND} pair.second = 2.5f64)\n"
    "    let floats: Floats = embed_floats(0.5f32)\n"
    "    @[ignore(4217)]\n"
    "    verdict(19u32, floats.x = 0.5f32 \N{LOGICAL AND} floats.y = 1.5f32 \N{LOGICAL AND} floats.z = 2.5f32)\n"
    "    let quad: Quad = embed_quad(70i64)\n"
    "    @[ignore(4217)]\n"
    "    verdict(20u32, quad.a = 70i64 \N{LOGICAL AND} quad.b = 0.5f64 \N{LOGICAL AND} quad.c = 69i32"
    " \N{LOGICAL AND} quad.d = 0.25f64)\n"
    "    let bytes: Bytes = embed_bytes(30u8)\n"
    "    verdict(21u32, bytes.v\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}0\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} = 30u8"
    " \N{LOGICAL AND} bytes.v\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}2\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} = 32u8"
    " \N{LOGICAL AND} bytes.tag = 513u16)\n"
    "    7u6\n";

  // A call of a function nobody defines.
  constexpr const char* missing = "@[host(\"embed_nobody_defines_this\")]\n"
    "fn absent(v: i64) \N{RIGHTWARDS ARROW} i64\n"
    "@[host(\"embed_verdict\")]\nfn verdict(which: u32, ok: bool)\n"
    "\n"
    "let std := \N{APL FUNCTIONAL SYMBOL QUAD}import(\"std\")\n"
    "\n"
    "@[startup, impure]\n"
    "fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:\n"
    "    verdict(1u32, true)\n"
    "    if absent(1i64) = 1i64: 1u6 else: 2u6\n";

  // A `str` crossing, which the checker made a function of its own for: what
  // a missing symbol says is about the function the program wrote.
  constexpr const char* missing_text = "@[host(\"embed_nobody_defines_that\")]\n"
    "fn absent_text(s: str) \N{RIGHTWARDS ARROW} i64\n"
    "\n"
    "let std := \N{APL FUNCTIONAL SYMBOL QUAD}import(\"std\")\n"
    "\n"
    "@[startup, impure]\n"
    "fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:\n"
    "    if absent_text(\"x\") = 1i64: 1u6 else: 2u6\n";

  // Programs that end before their startup function answers: through the
  // system's own call -- for the whole process, and for the thread -- and by
  // stopping, an answer that does not fit and an index past the end.  Each
  // ends the run and nothing more: the interpreter answers how it ended and
  // the program embedding it goes on, and what follows the end never runs.
  constexpr const char* exits_group = "@[host(\"embed_verdict\")]\nfn verdict(which: u32, ok: bool)\n"
    "\n"
    "let std := \N{APL FUNCTIONAL SYMBOL QUAD}import(\"std\")\n"
    "\n"
    "@[startup, impure]\n"
    "fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:\n"
    "    let gone: i64 = \N{APL FUNCTIONAL SYMBOL QUAD}syscall(\N{APL FUNCTIONAL SYMBOL QUAD}sc@exit_group, 5i64)\n"
    "    verdict(1u32, false)\n"
    "    1u6\n";

  constexpr const char* exits_thread = "@[host(\"embed_verdict\")]\nfn verdict(which: u32, ok: bool)\n"
    "\n"
    "let std := \N{APL FUNCTIONAL SYMBOL QUAD}import(\"std\")\n"
    "\n"
    "@[startup, impure]\n"
    "fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:\n"
    "    let gone: i64 = \N{APL FUNCTIONAL SYMBOL QUAD}syscall(\N{APL FUNCTIONAL SYMBOL QUAD}sc@exit, 6i64)\n"
    "    verdict(1u32, false)\n"
    "    1u6\n";

  constexpr const char* overflows = "@[host(\"embed_verdict\")]\nfn verdict(which: u32, ok: bool)\n"
    "\n"
    "let std := \N{APL FUNCTIONAL SYMBOL QUAD}import(\"std\")\n"
    "\n"
    "@[opaque]\n"
    "fn big() \N{RIGHTWARDS ARROW} u8:\n"
    "    200u8\n"
    "\n"
    "@[startup, impure]\n"
    "fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:\n"
    "    let sum: u8 = big() + big()\n"
    "    verdict(1u32, false)\n"
    "    if sum = 0u8: 1u6 else: 2u6\n";

  constexpr const char* past_the_end = "@[host(\"embed_verdict\")]\nfn verdict(which: u32, ok: bool)\n"
    "\n"
    "let std := \N{APL FUNCTIONAL SYMBOL QUAD}import(\"std\")\n"
    "\n"
    "@[opaque]\n"
    "fn where() \N{RIGHTWARDS ARROW} u64 \N{CURRENCY SIGN}idx:\n"
    "    7\n"
    "\n"
    "@[startup, impure]\n"
    "fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:\n"
    "    let l: [u8] = [1u8, 2u8, 3u8] in \N{APL FUNCTIONAL SYMBOL QUAD}static\n"
    "    let x: u8 = l\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}where()\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}\n"
    "    verdict(1u32, false)\n"
    "    if x = 0u8: 1u6 else: 2u6\n";

  // A program calling what the host hands over by name: a function nothing
  // exports, and one put where an exported symbol of that name is -- the
  // verdict, which then reaches what was handed over and not `embed_verdict`.
  constexpr const char* handed = "@[host(\"embed_hidden\")]\nfn hidden(n: i64) \N{RIGHTWARDS ARROW} i64\n"
    "@[host(\"embed_scaled\")]\nfn scaled(n: i32, x: f64) \N{RIGHTWARDS ARROW} f64\n"
    "@[host(\"embed_verdict\")]\nfn verdict(which: u32, ok: bool)\n"
    "\n"
    "let std := \N{APL FUNCTIONAL SYMBOL QUAD}import(\"std\")\n"
    "\n"
    "@[startup, impure]\n"
    "fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:\n"
    "    verdict(1u32, hidden(20i64) = 42i64)\n"
    "    @[ignore(4217)]\n"
    "    verdict(2u32, scaled(3i32, 0.5f64) = 10.5f64)\n"
    "    0u6\n";

  // What the program handed `handed` called in place of `embed_verdict`.
  std::vector<std::pair<std::uint32_t, bool>> handed_verdicts;

  // Twice and two more: of internal linkage, so that nothing but the table
  // can find it.
  std::int64_t hidden(std::int64_t n)
  {
    return 2 * n + 2;
  }

  // `n` and `x` added and multiplied by the factor `context` points at: a
  // function handed over with a context, which comes before what the
  // program's declaration says -- an integer and a floating-point number,
  // each where its convention puts it after the pointer.
  double scaled(void* context, std::int32_t n, double x)
  {
    return *static_cast<double*>(context) * (n + x);
  }

  double factor = 3.0;

  // Text answered by the host: copied out of what it keeps, a nul among the
  // bytes, nothing at all the empty string -- and a thousand copies made and
  // given back.
  constexpr const char* texts = "@[host(\"embed_greeting\")]\nfn greeting() \N{RIGHTWARDS ARROW} str\n"
    "@[host(\"embed_with_nul\")]\nfn with_nul() \N{RIGHTWARDS ARROW} str\n"
    "@[host(\"embed_empty\")]\nfn empty() \N{RIGHTWARDS ARROW} str\n"
    "@[host(\"embed_nothing\")]\nfn nothing() \N{RIGHTWARDS ARROW} str\n"
    "@[host(\"embed_echo\")]\nfn echo(s: str) \N{RIGHTWARDS ARROW} str\n"
    "@[host(\"embed_big\")]\nfn big(n: u64) \N{RIGHTWARDS ARROW} str\n"
    "@[host(\"embed_verdict\")]\nfn verdict(which: u32, ok: bool)\n"
    "\n"
    "let std := \N{APL FUNCTIONAL SYMBOL QUAD}import(\"std\")\n"
    "\n"
    "@[opaque, impure]\n"
    "fn bytes_of_big(n: u64) \N{RIGHTWARDS ARROW} u64:\n"
    "    let made: str = big(n)\n"
    "    \N{APL FUNCTIONAL SYMBOL QUAD}drop(#\N{APL FUNCTIONAL SYMBOL QUAD}bytes(made))\n"
    "\n"
    "@[startup, impure]\n"
    "fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:\n"
    "    verdict(1u32, greeting() = \"hello, world\")\n"
    "    let odd: str = with_nul()\n"
    "    verdict(2u32, odd = \"a\\u0000b\N{EURO SIGN}\" \N{LOGICAL AND}"
    " \N{APL FUNCTIONAL SYMBOL QUAD}drop(#\N{APL FUNCTIONAL SYMBOL QUAD}bytes(odd)) = 6u64)\n"
    "    verdict(3u32, empty() = \"\" \N{LOGICAL AND} nothing() = \"\")\n"
    "    verdict(4u32, echo(\"again\") = \"again!\")\n"
    "    let total: mut u64 = 0u64\n"
    "    foreach _: u64 = 0u64\N{HORIZONTAL ELLIPSIS}1000u64:\n"
    "        total \N{LEFTWARDS ARROW} total + bytes_of_big(4096u64)\n"
    "    verdict(5u32, total = 4096000u64)\n"
    "    0u6\n";

  // Text that is not UTF-8, which stops the program before anything reads it.
  constexpr const char* not_text = "@[host(\"embed_overlong\")]\nfn overlong() \N{RIGHTWARDS ARROW} str\n"
    "@[host(\"embed_verdict\")]\nfn verdict(which: u32, ok: bool)\n"
    "\n"
    "let std := \N{APL FUNCTIONAL SYMBOL QUAD}import(\"std\")\n"
    "\n"
    "@[startup, impure]\n"
    "fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:\n"
    "    let got: str = overlong()\n"
    "    verdict(1u32, false)\n"
    "    if got = \"\": 1u6 else: 2u6\n";

  // A program that would run for as long as there are numbers: a loop and
  // nothing else, which only a stop from outside ends.
  constexpr const char* forever = "let std := \N{APL FUNCTIONAL SYMBOL QUAD}import(\"std\")\n"
    "\n"
    "@[opaque]\n"
    "fn last() \N{RIGHTWARDS ARROW} u64:\n"
    "    18446744073709551615u64\n"
    "\n"
    "@[startup]\n"
    "fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:\n"
    "    let limit: u64 = last()\n"
    "    let n: mut u64 = 0u64\n"
    "    while n < limit:\n"
    "        n \N{LEFTWARDS ARROW} n + 1u64\n"
    "    if n = 0u64: 1u6 else: 2u6\n";

  // One that runs for longer than anyone waits with no loop at all: a
  // recursion, which only its calls give a stop the chance to end.
  constexpr const char* recursing = "let std := \N{APL FUNCTIONAL SYMBOL QUAD}import(\"std\")\n"
    "\n"
    "fn fib(n: u64) \N{RIGHTWARDS ARROW} u64:\n"
    "    if n < 2u64: n else: fib(n - 1u64) + fib(n - 2u64)\n"
    "\n"
    "@[startup]\n"
    "fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:\n"
    "    if fib(90u64) = 0u64: 1u6 else: 2u6\n";

  // A program stopped while the host function it called is still running,
  // which ends it as that function returns and before the next call.
  constexpr const char* stopped_inside = "@[host(\"embed_wait\")]\nfn wait() \N{RIGHTWARDS ARROW} i64\n"
    "@[host(\"embed_verdict\")]\nfn verdict(which: u32, ok: bool)\n"
    "\n"
    "let std := \N{APL FUNCTIONAL SYMBOL QUAD}import(\"std\")\n"
    "\n"
    "@[startup, impure]\n"
    "fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:\n"
    "    let got: i64 = wait()\n"
    "    verdict(1u32, false)\n"
    "    if got = 1i64: 1u6 else: 2u6\n";

  // Whether `waiting` returned, which a stop made while it ran waits for.
  bool waited = false;

  // Have another thread stop the interpreter `context` is, and return only
  // once it has, a while after.
  std::int64_t waiting(void* context)
  {
    std::thread([context] { static_cast<pl4g::embed::Interpreter*>(context)->stop(); }).join();
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    waited = true;
    return 1;
  }

  // A program that does not compile.
  constexpr const char* broken = "let std := \N{APL FUNCTIONAL SYMBOL QUAD}import(\"std\")\n"
    "\n"
    "@[startup]\n"
    "fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:\n"
    "    nothing_called_this\n";


  // What was written to the descriptor `fd` since it was made.
  std::string written(int fd)
  {
    std::string text;
    char buffer[4096];
    ::lseek(fd, 0, SEEK_SET);
    for (ssize_t n; (n = ::read(fd, buffer, sizeof buffer)) > 0;)
      text.append(buffer, std::size_t(n));
    return text;
  }

} // anonymous namespace


int main()
{
  {
    pl4g::embed::Interpreter interpreter;
    auto ended = interpreter.run_source(values, "embedded-values.pl4g");
    expect(ended.status == 7 && ended.signal == 0,
           std::format("the program of values ended with status {}, signal {}", ended.status, ended.signal));
    expect(seen.verdicts.size() == 21, std::format("{} verdicts of 21", seen.verdicts.size()));
    for (auto which : seen.failed)
      expect(false, std::format("comparison {} failed in the program", which));
    expect(seen.i8 == -100, std::format("i8 arrived as {}", seen.i8));
    expect(seen.i16 == -30000, std::format("i16 arrived as {}", seen.i16));
    expect(seen.i32 == -2000000000, std::format("i32 arrived as {}", seen.i32));
    expect(seen.i64 == -5000000000, std::format("i64 arrived as {}", seen.i64));
    expect(seen.u8 == 250, std::format("u8 arrived as {}", seen.u8));
    expect(seen.u16 == 65000, std::format("u16 arrived as {}", seen.u16));
    expect(seen.u32 == 4000000000u, std::format("u32 arrived as {}", seen.u32));
    expect(seen.u64 == 18000000000000000000u, std::format("u64 arrived as {}", seen.u64));
    expect(! seen.truth, "the last bool arrived as true");
    expect(seen.character == 'a', std::format("char arrived as {}", seen.character));
    expect(seen.single == 3.0f, std::format("f32 arrived as {}", seen.single));
    expect(seen.twice == 2.25, std::format("f64 arrived as {}", seen.twice));
    expect(seen.first == "host" && seen.second == "functions",
           std::format("the texts arrived as '{}' and '{}'", seen.first, seen.second));

    // Run again with the same interpreter: a run starts afresh, and finds
    // each function again.
    seen.verdicts.clear();
    ended = interpreter.run_source(values, "embedded-values.pl4g");
    expect(ended.status == 7 && seen.verdicts.size() == 21 && seen.calls == 2,
           std::format("the second run ended with status {}, {} verdicts, {} calls", ended.status,
                       seen.verdicts.size(), seen.calls));
  }

  // What a program says on the error device, caught where it can be read.
  auto caught = [](const char* text, std::string_view name) {
    int fd = ::memfd_create("embed-host", 0);
    pl4g::embed::Interpreter interpreter;
    interpreter.devices(0, 1, fd).colour(pl4g::embed::Colour::never);
    auto ended = interpreter.run_source(text, std::string(name));
    auto said = written(fd);
    ::close(fd);
    return std::pair{ ended, said };
  };

  {
    seen.verdicts.clear();
    auto [ended, said] = caught(missing, "embedded-missing.pl4g");
    expect(ended.status == 92 && ended.signal == 0,
           std::format("a missing symbol ended with status {}, signal {}", ended.status, ended.signal));
    expect(seen.verdicts.size() == 1, "what came before the missing symbol did not run");
    expect(said == "embedded-missing.pl4g:2:1: pl4g: the host defines no 'embed_nobody_defines_this' in 'absent'\n",
           std::format("a missing symbol said {:?}", said));
  }

  {
    auto [ended, said] = caught(missing_text, "embedded-missing-text.pl4g");
    expect(ended.status == 92, std::format("a missing symbol taking a str ended with status {}", ended.status));
    expect(said
             == "embedded-missing-text.pl4g:2:1: pl4g: the host defines no 'embed_nobody_defines_that' in "
                "'absent_text'\n",
           std::format("a missing symbol taking a str said {:?}", said));
  }

  // Each way of ending ends the run, with its status, and what came after it
  // did not run -- and this program is still here to see it.
  for (auto [text, name, status] : { std::tuple{ exits_group, "embedded-exit-group.pl4g", 5 },
                                     std::tuple{ exits_thread, "embedded-exit.pl4g", 6 },
                                     std::tuple{ overflows, "embedded-overflow.pl4g", 80 },
                                     std::tuple{ past_the_end, "embedded-past-the-end.pl4g", 82 } }) {
    seen.verdicts.clear();
    auto [ended, said] = caught(text, name);
    expect(ended.status == status && ended.signal == 0,
           std::format("{} ended with status {}, signal {}, where {} was due", name, ended.status, ended.signal,
                       status));
    expect(seen.verdicts.empty(), std::format("{} went on past its end", name));
  }

  {
    seen.verdicts.clear();
    pl4g::embed::Interpreter interpreter;
    interpreter.define("embed_hidden", hidden)
      .define("embed_scaled", scaled, &factor)
      .define("embed_verdict", +[](std::uint32_t which, bool ok) { handed_verdicts.emplace_back(which, ok); });
    auto ended = interpreter.run_source(handed, "embedded-handed.pl4g");
    expect(ended.status == 0, std::format("a program calling what was handed over ended with status {}", ended.status));
    expect(handed_verdicts == std::vector<std::pair<std::uint32_t, bool>>{ { 1, true }, { 2, true } },
           std::format("what was handed over saw {} verdicts", handed_verdicts.size()));
    expect(seen.verdicts.empty(), "the exported verdict was called where one was handed over");
  }

  {
    seen.verdicts.clear();
    seen.failed.clear();
    pl4g::embed::Interpreter interpreter;
    auto ended = interpreter.run_source(texts, "embedded-texts.pl4g");
    expect(ended.status == 0 && ended.signal == 0,
           std::format("the program of texts ended with status {}, signal {}", ended.status, ended.signal));
    expect(seen.verdicts.size() == 5, std::format("{} verdicts of 5 on texts", seen.verdicts.size()));
    for (auto which : seen.failed)
      expect(false, std::format("comparison {} of texts failed in the program", which));
  }

  {
    seen.verdicts.clear();
    auto [ended, said] = caught(not_text, "embedded-not-text.pl4g");
    expect(ended.status == 94 && ended.signal == 0,
           std::format("text that is not UTF-8 ended with status {}, signal {}", ended.status, ended.signal));
    expect(seen.verdicts.empty(), "text that is not UTF-8 was read");
    expect(said.contains("text the host answered that is not UTF-8"),
           std::format("text that is not UTF-8 said {:?}", said));
  }

  // A run stopped from another thread ends with its status, and the program
  // embedding it goes on; the stop is that run's, and the next is not
  // stopped by it.
  {
    int fd = ::memfd_create("embed-host", 0);
    pl4g::embed::Interpreter interpreter;
    interpreter.devices(0, 1, fd).colour(pl4g::embed::Colour::never);
    auto began = std::chrono::steady_clock::now();
    std::jthread stopper([&interpreter] {
      std::this_thread::sleep_for(std::chrono::milliseconds(200));
      interpreter.stop();
    });
    auto ended = interpreter.run_source(forever, "embedded-forever.pl4g");
    auto took = std::chrono::steady_clock::now() - began;
    stopper.join();
    auto said = written(fd);
    ::close(fd);
    expect(ended.status == 93 && ended.signal == 0,
           std::format("a program stopped ended with status {}, signal {}", ended.status, ended.signal));
    expect(said == "pl4g: the host stopped the program\n", std::format("a program stopped said {:?}", said));
    expect(took < std::chrono::seconds(60), "a program stopped took a minute to end");

    // A recursion, stopped at a call, by the same interpreter: the stop
    // before was that run's.
    std::jthread again([&interpreter] {
      std::this_thread::sleep_for(std::chrono::milliseconds(200));
      interpreter.stop();
    });
    ended = interpreter.run_source(recursing, "embedded-recursing.pl4g");
    again.join();
    expect(ended.status == 93, std::format("a recursion stopped ended with status {}", ended.status));

    // Asked for while nothing runs, which is for no run.
    interpreter.stop();
    seen.verdicts.clear();
    ended = interpreter.run_source(missing, "embedded-after-a-stop.pl4g");
    expect(ended.status == 92 && seen.verdicts.size() == 1,
           std::format("a run after a stop ended with status {} and {} verdicts", ended.status, seen.verdicts.size()));
  }

  {
    seen.verdicts.clear();
    int fd = ::memfd_create("embed-host", 0);
    pl4g::embed::Interpreter interpreter;
    interpreter.define("embed_wait", waiting, &interpreter).devices(0, 1, fd);
    auto ended = interpreter.run_source(stopped_inside, "embedded-stopped-inside.pl4g");
    ::close(fd);
    expect(ended.status == 93, std::format("a program stopped in a host call ended with status {}", ended.status));
    expect(waited, "a host call was interrupted by a stop");
    expect(seen.verdicts.empty(), "a program stopped in a host call went on after it");
  }

  {
    auto [ended, said] = caught(broken, "embedded-broken.pl4g");
    expect(ended.status == 1 && ended.signal == 0,
           std::format("a program that does not compile ended with status {}", ended.status));
    expect(said.contains("embedded-broken.pl4g:5:5"), std::format("a program that does not compile said {:?}", said));
  }

  if (wrong == 0)
    std::println("embed-host: everything crossed as it should");
  return wrong == 0 ? 0 : 1;
}
