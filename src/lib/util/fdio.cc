#include "util/fdio.hh"

#include <cerrno>

#include <unistd.h>


namespace pl4g {

  bool write_all(int fd, std::string_view text) noexcept
  {
    while (! text.empty()) {
      auto n = ::write(fd, text.data(), text.size());
      if (n < 0) [[unlikely]] {
        if (errno == EINTR)
          continue;
        return false;
      }
      text.remove_prefix(std::size_t(n));
    }
    return true;
  }


  bool FdWriter::flush() noexcept
  {
    bool ok = write_all(fd, buffer);
    buffer.clear();
    return ok;
  }

} // namespace pl4g
