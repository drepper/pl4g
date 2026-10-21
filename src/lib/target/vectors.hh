// Bringing an operation over a whole run of elements down to what a machine
// has: as it stands, in pieces of a register's worth, or an element at a time.
// A port of pypl4g/target/vectors.py.
#pragma once

#include <map>
#include <set>
#include <vector>

#include "ir/ir.hh"
#include "ir/layout.hh"


namespace pl4g::target {

  // What one target can do to a run of elements at once: how wide its
  // registers holding one are (nought for none), and for each operation the
  // lane widths it has it at.
  struct Vectors {
    unsigned bits = 0;
    std::map<ir::BinOp, std::set<unsigned>> binary;
    std::map<ir::UnOp, std::set<unsigned>> unary;
    // The lane widths it can compare at.
    std::set<unsigned> compares;
    // Whether it can gather a register of truth values into the bits of an
    // ordinary one, one bit per lane, which is what `⎕mask` asks.
    bool masks = false;

    // How many elements of this type fit in one of its registers, for an
    // operation it has at `widths`.
    std::uint64_t lanes_at_once(const ir::Type* element, const ir::DataLayout& layout,
                                const std::set<unsigned>& widths) const;
  };

  // Every width a lane can be.
  inline const std::set<unsigned> every_width{ 8, 16, 32, 64 };

  // The fewest bytes worth taking as a piece.
  inline constexpr std::uint64_t smallest_piece = 4;

  // Rewrite every run-at-a-time operation of `module` into what `able` says it
  // can do.
  void settle(ir::Module& module, const Vectors& able, const ir::DataLayout& layout);

  // Whether `func` has anything to do with a run of elements held as one value
  // -- asked before they are brought down to what a machine has.
  bool works_on_runs(const ir::Function& func);

} // namespace pl4g::target
