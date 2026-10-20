// SHA-256 (FIPS 180-4), for what the bill of materials hashes.
#pragma once

#include <array>
#include <cstdint>
#include <span>
#include <string>
#include <string_view>


namespace pl4g {

  // A digest made a piece at a time, as Python's `hashlib.sha256()` is.
  class Sha256 {
  public:
    Sha256() noexcept;

    void update(std::span<const unsigned char> data) noexcept;
    void update(std::string_view text) noexcept
    {
      update({ reinterpret_cast<const unsigned char*>(text.data()), text.size() });
    }

    // The digest of all that was given, as lower-case hex: `hexdigest()`.
    std::string hexdigest() const;

  private:
    void block(const unsigned char* at) noexcept;

    std::array<std::uint32_t, 8> state;
    std::array<unsigned char, 64> buffer{};
    std::size_t held = 0;
    std::uint64_t length = 0;
  };

} // namespace pl4g
