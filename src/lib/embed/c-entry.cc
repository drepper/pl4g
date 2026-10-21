// The functions libpl4g-embed.so offers with C's linkage.
//
// They are in the shared object only, which is built with every other symbol
// hidden: what a program finds with dlsym is these and nothing of the
// compiler's own.  A state is an interpreter, which holds everything a run
// leaves behind (embed/embed.hh); the type the C side names is this one
// struct around it, which it never sees inside.
#include "embed/pl4g-embed.h"

#include <exception>
#include <new>
#include <print>
#include <string_view>

#include "embed/embed.hh"


struct pl4g_embed_state {
  pl4g::embed::Interpreter interpreter;
};


extern "C" [[gnu::visibility("default")]] pl4g_embed_state* pl4g_embed_state_new()
{
  return new (std::nothrow) pl4g_embed_state;
}


extern "C" [[gnu::visibility("default")]] void pl4g_embed_state_free(pl4g_embed_state* state)
{
  delete state;
}


extern "C" [[gnu::visibility("default")]] int pl4g_embed_run_source(pl4g_embed_state* state, const char* text,
                                                                   std::size_t length, const char* name)
{
  if (state == nullptr || text == nullptr) [[unlikely]]
    return -1;
  try {
    auto ended = state->interpreter.run_source(std::string_view(text, length),
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
