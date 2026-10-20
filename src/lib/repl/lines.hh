// Reading what is typed, a line at a time, from a descriptor the caller chose.
#pragma once

#include <memory>
#include <optional>
#include <string>
#include <string_view>


namespace pl4g::repl {

  // Lines from a descriptor: edited with libedit, and remembered, where the
  // descriptor is a terminal; read as they come where it is not.
  class LineReader {
  public:
    // Read from `input`; a terminal's prompts and echo go to `output`.
    LineReader(int input, int output, int error);
    LineReader(const LineReader&) = delete;
    LineReader& operator=(const LineReader&) = delete;
    ~LineReader();

    // The next line, without its end, shown `prompt` first where a terminal
    // is there to see it; nothing at the end of the input.
    std::optional<std::string> read(std::string_view prompt);
    // Remember `entry` for the terminal's history.
    void remember(std::string_view entry);

  private:
    struct Edit;
    std::unique_ptr<Edit> edit;
    int input;
    std::string pending;
    bool ended = false;
  };

} // namespace pl4g::repl
