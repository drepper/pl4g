// What went into the binary, written into the binary: pypl4g's sbom.py.
//
// Every image carries a `.sbom` section naming the compiler, every source that
// was read -- hashed by its bytes -- and every definition that was compiled,
// each hashed by its tokens, the layout taken out of them so that two
// spellings of one program have one hash.
#pragma once

#include <cstdint>
#include <span>
#include <string>
#include <string_view>
#include <vector>

#include "front/token.hh"
#include "source/manager.hh"


namespace pl4g::driver::sbom {

  // What pypl4g calls itself, which the compiler's row hashes: the image of
  // one has to be the image of the other for the two to be compared.
  inline constexpr std::string_view compiler_id = "pypl4g 0.1";

  // Where the table and its strings go.
  inline constexpr std::string_view section = ".sbom";
  inline constexpr std::string_view strings = ".sbomstr";
  // Each row is three four-byte fields: where the hash is, what the row is
  // about, and where the name is.
  inline constexpr std::uint64_t row_size = 12;

  // What one row of the table is about.
  enum class Tag : std::uint32_t {
    compiler = 1, source = 2, sources = 3, function = 4, type = 5, variable = 6, unit = 7,
  };

  // One row: what is hashed, what it is called, and the hash.
  struct Entry {
    Tag tag;
    std::string name;
    std::string digest;
  };

  // The bytes of the two sections, and what is in them.
  struct Table {
    std::vector<unsigned char> rows;
    std::vector<unsigned char> strings;
    std::vector<Entry> entries;
  };

  // The token stream as what it means, with the layout taken out of it.
  std::vector<std::string> normalized(std::span<const Token> tokens);
  // The hash of a normalized token stream, of a string, of several hashes.
  std::string digest_of(std::span<const Token> tokens);
  std::string digest_of_text(std::string_view text);
  std::string digest_of_digests(std::span<const std::string> digests);

  // Every row the table holds, in the order it holds them, for the units
  // `sources` says were read; the compiler's row says `compiler`.
  std::vector<Entry> entries_for(const SourceManager& sources, std::string_view compiler = compiler_id);

  // The table and its strings, laid out as they go into the image.
  Table build(std::span<const Entry> entries, bool little_endian);

} // namespace pl4g::driver::sbom
