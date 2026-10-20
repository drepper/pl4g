// Fragments: the pieces a section is built from.  A port of
// pypl4g/mc/fragment.py.
#pragma once

#include <cstdint>
#include <vector>

#include "mc/fixup.hh"
#include "mc/inst.hh"
#include "mc/symbol.hh"


namespace pl4g::mc {

  // Base of everything a section is made of.
  struct MCFragment {
    enum class Kind : std::uint8_t { data, inst, align, padding };
    Kind kind;
    MCSection* section = nullptr;
    // Offset within the section, filled in by the layout.
    std::uint64_t offset = 0;

    explicit MCFragment(Kind kind) noexcept : kind(kind) {}
    virtual ~MCFragment() = default;
    // The number of bytes this fragment occupies.
    virtual std::uint64_t size() const noexcept = 0;
    // The bytes this fragment contributes to the image.
    virtual Bytes bytes_of() const = 0;
  };

  // A run of literal bytes.
  struct MCDataFragment final : MCFragment {
    Bytes contents;
    std::vector<MCFixup> fixups;

    MCDataFragment() noexcept : MCFragment(Kind::data) {}
    std::uint64_t size() const noexcept override { return contents.size(); }
    Bytes bytes_of() const override { return contents; }
  };

  // One instruction, whose encoding may still change during relaxation.
  struct MCInstFragment final : MCFragment {
    InstRef inst;
    Bytes encoded;
    std::vector<MCFixup> fixups;
    // The wider form to grow into when a displacement does not reach.
    bool relaxable = false;

    MCInstFragment() noexcept : MCFragment(Kind::inst) {}
    std::uint64_t size() const noexcept override { return encoded.size(); }
    Bytes bytes_of() const override { return encoded; }
  };

  // Padding that brings the next fragment to an alignment boundary.
  struct MCAlignFragment final : MCFragment {
    std::uint64_t alignment = 1;
    // A trap instruction, so that falling into padding stops rather than drifts.
    std::uint8_t fill = 0xCC;
    std::uint64_t padding = 0;

    MCAlignFragment() noexcept : MCFragment(Kind::align) {}
    std::uint64_t size() const noexcept override { return padding; }
    // Record the padding the layout computed.
    void set_size(std::uint64_t value) noexcept { padding = value; }
    Bytes bytes_of() const override { return Bytes(padding, fill); }
  };

  // Growth slack reserved after a function, for later in-place patching.
  struct MCPaddingFragment final : MCFragment {
    std::uint64_t reserved = 0;
    std::uint8_t fill = 0xCC;

    MCPaddingFragment() noexcept : MCFragment(Kind::padding) {}
    std::uint64_t size() const noexcept override { return reserved; }
    Bytes bytes_of() const override { return Bytes(reserved, fill); }
  };

  // The fixups of a fragment that has any, and nothing for one that has none.
  std::vector<MCFixup>* fixups_of(MCFragment& fragment) noexcept;
  // The bytes a fixup of a fragment patches.
  Bytes* patched_bytes_of(MCFragment& fragment) noexcept;

} // namespace pl4g::mc
