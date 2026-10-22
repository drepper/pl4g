// PL4G's interpreter, for a C++ program that loads it as a shared object.
//
// The C++ face of embed/pl4g-embed.h: one object is one interpreter.  Making
// it loads libpl4g-embed.so, finds the four functions the library offers and
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


// What a function asks of its caller and promises in return, as contracts
// where the compiler has them (C++26's, P2900) and as nothing where it has
// not: a program including this need not be compiled with them.  PL4G_POST
// takes what `post` takes, a name for the result first where one is wanted.
//
// Where the compiler has them, the program has to be linked with what reports
// a violation.  GCC has them in C++26 whether or not -fcontracts is given, but
// links what reports one (libstdc++exp) only where it is, so a program using
// this is compiled and linked with -fcontracts -- or defines
// PL4G_NO_CONTRACTS, which leaves the conditions unsaid.
#if defined(__cpp_contracts) && __cpp_contracts >= 202502L && ! defined(PL4G_NO_CONTRACTS)
# define PL4G_PRE(...) pre(__VA_ARGS__)
# define PL4G_POST(...) post(__VA_ARGS__)
#else
# define PL4G_PRE(...)
# define PL4G_POST(...)
#endif


namespace pl4g::embed {

  // The interpreter in libpl4g-embed.so, with a state of its own.
  class LoadedInterpreter {
  public:
    // What is loaded where no other library is named: found the way the
    // dynamic loader finds a library.
    static constexpr const char* default_library = "libpl4g-embed.so";

    // Load `library`, find what it offers and make a state.  Throws
    // std::runtime_error saying which of them failed, having given back
    // whatever the ones before it took.
    explicit LoadedInterpreter(const char* library = default_library)
      PL4G_PRE(library != nullptr)
      PL4G_POST(valid())
    {
      handle = ::dlopen(library, RTLD_NOW);
      if (handle == nullptr) [[unlikely]]
        throw std::runtime_error(std::string("cannot load ") + library + ": " + ::dlerror());
      state_new = found<::pl4g_embed_state_new_t>("pl4g_embed_state_new");
      state_free = found<::pl4g_embed_state_free_t>("pl4g_embed_state_free");
      run_source = found<::pl4g_embed_run_source_t>("pl4g_embed_run_source");
      define_one = found<::pl4g_embed_define_t>("pl4g_embed_define");
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
      PL4G_POST(! other.valid())
      : handle(std::exchange(other.handle, nullptr)), state_new(other.state_new), state_free(other.state_free),
        run_source(other.run_source), define_one(other.define_one), state(std::exchange(other.state, nullptr))
    {
    }

    LoadedInterpreter& operator=(LoadedInterpreter&& other) noexcept
      PL4G_POST(this == &other || ! other.valid())
    {
      if (this != &other) {
        release();
        handle = std::exchange(other.handle, nullptr);
        state_new = other.state_new;
        state_free = other.state_free;
        run_source = other.run_source;
        define_one = other.define_one;
        state = std::exchange(other.state, nullptr);
      }
      return *this;
    }

    // Give back the state, and everything its runs took, and the library.
    ~LoadedInterpreter() { release(); }

    // Whether this holds an interpreter, which one moved from does not.
    bool valid() const noexcept { return state != nullptr; }

    // Hand over `function` as what a program's `@[host("SYMBOL")]` names with
    // `symbol`, asked before dlsym is: a function this program does not
    // export, or one put where an exported symbol of that name is.  It has to
    // answer, with the system's convention, what the declaration says, and
    // to take it -- after `context`, as a `void*`, where `context` is not
    // null, which is how one function serves several objects of this
    // program's.  Answers whether it was kept.
    template<typename R, typename... A>
    bool define(const std::string& symbol, R (*function)(A...), void* context = nullptr)
      PL4G_PRE(valid())
      PL4G_PRE(function != nullptr)
    {
      if (state == nullptr) [[unlikely]]
        return false;
      return define_one(state, symbol.c_str(), reinterpret_cast<void (*)()>(function), context) == 0;
    }

    // Compile the PL4G program `text` and run it in this interpreter's
    // state, `name` being what its diagnostics and faults call the file.
    // Answers what pl4g_embed_run_source answers: the status it exited with,
    // 1 where it did not compile, 128 plus a signal's number, or -1 where
    // the interpreter itself failed.
    // Asked of an interpreter that has not been moved from; one that has
    // answers -1 where nothing checks.
    int run(std::string_view text, const std::string& name = "embedded.pl4g")
      PL4G_PRE(valid())
      PL4G_PRE(text.data() != nullptr || text.empty())
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
      PL4G_PRE(handle != nullptr && symbol != nullptr)
      PL4G_POST(f: f != nullptr)
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
      PL4G_POST(handle == nullptr && state == nullptr)
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
    ::pl4g_embed_define_t define_one = nullptr;
    ::pl4g_embed_state* state = nullptr;
  };

} // namespace pl4g::embed
