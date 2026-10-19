// Output to a descriptor the caller chose.
//
// Nothing in the library writes to 1 or 2 by name: whoever embeds it says
// where diagnostics and a REPL's answers go, and may have put something else on
// the standard descriptors.  A writer collects formatted text and hands it to
// the descriptor in as few calls as the text allows.
#pragma once

#include <format>
#include <string>
#include <string_view>


namespace pl4g {

  // Formatted output to a file descriptor, buffered until flushed.
  class FdWriter {
  public:
    // Write to `fd`, which stays the caller's: it is not closed here.
    explicit FdWriter(int fd) noexcept : fd(fd) {}
    FdWriter(const FdWriter&) = delete;
    FdWriter& operator=(const FdWriter&) = delete;
    ~FdWriter() { flush(); }

    // Format into the buffer, as `std::print` would.
    template<typename... Args>
    void print(std::format_string<Args...> fmt, Args&&... args)
    {
      std::format_to(std::back_inserter(buffer), fmt, std::forward<Args>(args)...);
    }

    // Format into the buffer and end the line.
    template<typename... Args>
    void println(std::format_string<Args...> fmt, Args&&... args)
    {
      print(fmt, std::forward<Args>(args)...);
      buffer += '\n';
    }

    // Append text as it is.
    void write(std::string_view text) { buffer += text; }

    // Hand everything buffered to the descriptor.  Returns false if the
    // descriptor would not take it all.
    bool flush() noexcept;

    // The descriptor written to.
    int descriptor() const noexcept { return fd; }

  private:
    int fd;
    std::string buffer;
  };


  // Write all of `text` to `fd`, retrying after interruptions and partial
  // writes.  Returns false on an error.
  bool write_all(int fd, std::string_view text) noexcept;

} // namespace pl4g
