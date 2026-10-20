// Reading what is typed, a line at a time.
#include "repl/lines.hh"

#include <histedit.h>
#include <unistd.h>

#include <cerrno>
#include <cstdio>
#include <utility>


namespace pl4g::repl {

  // The editor and its history, where the input is a terminal.
  struct LineReader::Edit {
    ::EditLine* el = nullptr;
    ::History* history = nullptr;
    ::HistEvent event{};
    std::FILE* in = nullptr;
    std::FILE* out = nullptr;
    std::FILE* err = nullptr;
    std::string prompt;

    ~Edit()
    {
      if (history != nullptr)
        ::history_end(history);
      if (el != nullptr)
        ::el_end(el);
      for (auto f : { in, out, err })
        if (f != nullptr)
          std::fclose(f);
    }
  };


  namespace {

    // What libedit asks for the prompt: the one the reader was handed last.
    const char* prompt_of(::EditLine* el)
    {
      void* data = nullptr;
      ::el_get(el, EL_CLIENTDATA, &data);
      return data != nullptr ? static_cast<std::string*>(data)->c_str() : "";
    }

  } // anonymous namespace


  LineReader::LineReader(int input, int output, int error) : input(input)
  {
    if (::isatty(input) == 0)
      return;
    auto made = std::make_unique<Edit>();
    // libedit wants streams; its own, on copies of the descriptors, so that
    // closing them leaves the caller's alone.
    made->in = ::fdopen(::dup(input), "r");
    made->out = ::fdopen(::dup(output), "w");
    made->err = ::fdopen(::dup(error), "w");
    if (made->in == nullptr || made->out == nullptr || made->err == nullptr) [[unlikely]]
      return;
    made->el = ::el_init_fd("pl4g", made->in, made->out, made->err, ::fileno(made->in), ::fileno(made->out),
                            ::fileno(made->err));
    if (made->el == nullptr) [[unlikely]]
      return;
    made->history = ::history_init();
    ::history(made->history, &made->event, H_SETSIZE, 1000);
    ::el_set(made->el, EL_HIST, ::history, made->history);
    ::el_set(made->el, EL_EDITOR, "emacs");
    ::el_set(made->el, EL_CLIENTDATA, &made->prompt);
    ::el_set(made->el, EL_PROMPT, prompt_of);
    ::el_source(made->el, nullptr);
    edit = std::move(made);
  }


  LineReader::~LineReader() = default;


  std::optional<std::string> LineReader::read(std::string_view prompt)
  {
    if (edit != nullptr) {
      edit->prompt = prompt;
      int count = 0;
      const char* line = ::el_gets(edit->el, &count);
      if (line == nullptr || count <= 0)
        return std::nullopt;
      std::string found(line, std::size_t(count));
      if (! found.empty() && found.back() == '\n')
        found.pop_back();
      return found;
    }
    // A line at a time from whatever the descriptor is, keeping what came
    // after the end of the line for the next.
    while (true) {
      if (auto end = pending.find('\n'); end != std::string::npos) {
        std::string found = pending.substr(0, end);
        pending.erase(0, end + 1);
        return found;
      }
      if (ended) {
        if (pending.empty())
          return std::nullopt;
        return std::exchange(pending, {});
      }
      char buffer[4096];
      auto got = ::read(input, buffer, sizeof buffer);
      if (got < 0 && errno == EINTR)
        continue;
      if (got <= 0)
        ended = true;
      else
        pending.append(buffer, std::size_t(got));
    }
  }


  void LineReader::remember(std::string_view entry)
  {
    if (edit != nullptr && ! entry.empty())
      ::history(edit->history, &edit->event, H_ENTER, std::string(entry).c_str());
  }

} // namespace pl4g::repl
