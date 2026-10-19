// Where the bytes of a value are, on a given target.
//
// A port of pypl4g's ir/layout.py: sizes, alignments, and where each part of a
// value lies, by the same rules -- a record's fields in the order declared
// unless the system lays it out, a sum's payload with the tag after it.
#pragma once

#include <cstdint>
#include <unordered_map>
#include <vector>

#include "ir/types.hh"


namespace pl4g::ir {

  // What a target says about laying things out.
  struct DataLayout {
    unsigned pointer_size = 8;
    bool little_endian = true;
    // Whether the system's C ABI lays out what is being asked about.
    bool system = false;
    // How large each generator's frame came to, which the pass splitting it
    // measured; a frame has no other size.
    const std::unordered_map<const CoroutineFrame*, std::uint64_t>* frames = nullptr;
  };

  // A fixed array this many bytes or larger is aligned to this many, where the
  // language rather than the system lays it out.
  inline constexpr std::uint64_t wide_enough = 16;

  // Asked of a type that has no layout: the memory token, a type that could
  // not be worked out.
  struct NoLayout {
    const Type* ty;
  };

  std::uint64_t size_of(const Type* ty, const DataLayout& layout);
  std::uint64_t align_of(const Type* ty, const DataLayout& layout);
  // The size rounded up to the alignment: how far apart two in a row are.
  std::uint64_t stride_of(const Type* ty, const DataLayout& layout);
  // Where each field of a record begins.
  std::vector<std::uint64_t> offsets_of(const ProductType* ty, const DataLayout& layout);
  // Where each member of a tuple begins.
  std::vector<std::uint64_t> member_offsets_of(const TupleType* ty, const DataLayout& layout);
  // Where each of `parts_of(ty)` lies in memory.
  std::vector<std::uint64_t> part_offsets_of(const Type* ty, const DataLayout& layout);
  // Where each of `leaves_of(ty)` lies in memory.
  std::vector<std::uint64_t> leaf_offsets_of(const Type* ty, const DataLayout& layout);
  // Where a sum's or a result's tag is, and a result's error.
  std::uint64_t tag_offset_of(const Type* ty, const DataLayout& layout);
  std::uint64_t error_offset_of(const ResultType* ty, const DataLayout& layout);

} // namespace pl4g::ir
