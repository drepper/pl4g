// PL4G's interpreter, for a C++ program that loads it as a shared object.
//
// The C++ face of embed/pl4g-embed.h: one object is one interpreter.  Making
// it loads libpl4g-embed.so, finds the three functions the library offers and
// makes a state of the interpreter; destroying it gives the state back and
// lets the library go.  Several may exist at once, each with a state of its
// own, the library being loaded once however many hold it.
//
// Header only, and needing nothing of the compiler's but the C header: what a
// program includes to embed the interpreter is this and <dlfcn.h>.  It is not
// pl4g::embed::Interpreter (embed/embed.hh), which is the interpreter linked
// into a program, with its settings; this one is the interpreter as the
// shared object offers it.
//
// The functions the PL4G program calls with @[host("SYMBOL")] are found with
// dlsym(RTLD_DEFAULT, SYMBOL), so the program loading this has to be linked
// with -rdynamic for its own to be found.
#pragma once

#include <dlfcn.h>

#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include "embed/pl4g-embed.h"


namespace pl4g::embed {

  // The interpreter in libpl4g-embed.so, with a state of its own.
  class LoadedInterpreter {
  public:
    // What is loaded where no other library is named: found the way the
    // dynamic loader finds a library.
    static constexpr const char* default_library = "libpl4g-embed.so";

    // Load `library`, find what it offers and make a state.  Throws
    // std::runtime_error saying which of the three failed, having given back
    // whatever the ones before it took.
    explicit LoadedInterpreter(const char* library = default_library)
    {
      handle = ::dlopen(library, RTLD_NOW);
      if (handle == nullptr) [[unlikely]]
        throw std::runtime_error(std::string("cannot load ") + library + ": " + ::dlerror());
      state_new = found<::pl4g_embed_state_new_t>("pl4g_embed_state_new");
      state_free = found<::pl4g_embed_state_free_t>("pl4g_embed_state_free");
      run_source = found<::pl4g_embed_run_source_t>("pl4g_embed_run_source");
      state = state_new();
      if (state == nullptr) [[unlikely]] {
        ::dlclose(handle);
        throw std::runtime_error("no state of the interpreter could be made");
      }
    }

    LoadedInterpreter(const LoadedInterpreter&) = delete;
    LoadedInterpreter& operator=(const LoadedInterpreter&) = delete;

    // The other's state and its hold on the library, the other left with
    // neither.
    LoadedInterpreter(LoadedInterpreter&& other) noexcept
      : handle(std::exchange(other.handle, nullptr)), state_new(other.state_new), state_free(other.state_free),
        run_source(other.run_source), state(std::exchange(other.state, nullptr))
    {
    }

    LoadedInterpreter& operator=(LoadedInterpreter&& other) noexcept
    {
      if (this != &other) {
        release();
        handle = std::exchange(other.handle, nullptr);
        state_new = other.state_new;
        state_free = other.state_free;
        run_source = other.run_source;
        state = std::exchange(other.state, nullptr);
      }
      return *this;
    }

    // Give back the state, and everything its runs took, and the library.
    ~LoadedInterpreter() { release(); }

    // Compile the PL4G program `text` and run it in this interpreter's
    // state, `name` being what its diagnostics and faults call the file.
    // Answers what pl4g_embed_run_source answers: the status it exited with,
    // 1 where it did not compile, 128 plus a signal's number, or -1 where
    // the interpreter itself failed.
    int run(std::string_view text, const std::string& name = "embedded.pl4g")
    {
      if (state == nullptr) [[unlikely]]
        return -1;
      return run_source(state, text.data(), text.size(), name.c_str());
    }

  private:
    // The function the library offers as `symbol`.  Throws where it offers
    // none, having let the library go.
    template<typename F>
    F found(const char* symbol)
    {
      void* address = ::dlsym(handle, symbol);
      if (address == nullptr) [[unlikely]] {
        std::string why = std::string("the library offers no ") + symbol;
        ::dlclose(handle);
        handle = nullptr;
        throw std::runtime_error(why);
      }
      return reinterpret_cast<F>(address);
    }

    // Give back what this holds, which a moved-from object holds nothing of.
    void release() noexcept
    {
      if (state != nullptr)
        state_free(std::exchange(state, nullptr));
      if (handle != nullptr)
        ::dlclose(std::exchange(handle, nullptr));
    }

    void* handle = nullptr;
    ::pl4g_embed_state_new_t state_new = nullptr;
    ::pl4g_embed_state_free_t state_free = nullptr;
    ::pl4g_embed_run_source_t run_source = nullptr;
    ::pl4g_embed_state* state = nullptr;
  };

} // namespace pl4g::embed
