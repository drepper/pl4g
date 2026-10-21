// The one function libpl4g-embed.so offers with C's linkage.
//
// It is in the shared object only, which is built with every other symbol
// hidden: what a program finds with dlsym is this and nothing of the
// compiler's own.
#include "embed/pl4g-embed.h"

#include <exception>
#include <print>
#include <string_view>

#include "embed/embed.hh"


extern "C" [[gnu::visibility("default")]] int pl4g_embed_run_source(const char* text, std::size_t length,
                                                                   const char* name)
{
  try {
    pl4g::embed::Interpreter interpreter;
    auto ended = interpreter.run_source(std::string_view(text, length),
                                        name != nullptr ? name : "embedded.pl4g");
    return ended.signal != 0 ? 128 + ended.signal : ended.status;
  } catch (const std::exception& error) {
    // Nothing a C caller could catch may leave here.
    std::print(stderr, "pl4g-embed: {}\n", error.what());
    return -1;
  } catch (...) {
    std::print(stderr, "pl4g-embed: the interpreter failed\n");
    return -1;
  }
}
