// How the system's C answers a record, on each architecture.  A port of
// pypl4g/ir/cabi.py.
//
// A record marked `@[abi]` answered by a function of the system's convention is
// answered the way a C compiler answers the `struct` it is laid out as: on
// x86-64 by eightbytes in `rax`/`rdx` and `xmm0`/`xmm1` by what each holds, on
// AArch64 a homogeneous floating-point aggregate a member to a register and
// anything else in `x0`/`x1`, on RISC-V one or two floating-point members (and
// an integer beside one) by kind and anything else in `a0`/`a1` -- and a record
// none of those take through room the caller makes, its address handed over
// first.  This is only the classification; `largeanswers` rewrites.
#pragma once

#include <cstdint>
#include <optional>
#include <string_view>
#include <utility>
#include <vector>

#include "ir/ir.hh"
#include "ir/layout.hh"
#include "ir/types.hh"


namespace pl4g::ir::cabi {

  // The layout C lays a record out with.
  inline constexpr DataLayout system_layout{ .pointer_size = 8, .little_endian = true, .system = true };

  // The most a record answered in registers takes, but for AArch64's
  // homogeneous aggregates.
  inline constexpr std::uint64_t in_registers = 16;

  // One register's worth of a record answered in registers: which bytes of the
  // record it holds, and as what -- a member's own type where it is exactly
  // that member, `u64` for several or for a floating-point number in an
  // ordinary register, `f64` for x86-64's two `float`s of one eightbyte.
  struct Piece {
    const Type* ty = nullptr;
    std::uint64_t offset = 0;
    std::uint64_t size = 0;
  };

  // The architecture a triple names, which is its first component.
  std::string_view architecture(std::string_view triple) noexcept;

  // Whether `cconv` is the system's convention on `triple`'s architecture.
  bool follows_system(std::string_view triple, std::string_view cconv) noexcept;

  // Whether a function answering through room its caller made answers with
  // where that room is, as x86-64's convention has it.
  bool answers_its_place(std::string_view triple) noexcept;

  // Whether `ty` is one value a register holds as C would: a number, a truth
  // value, a character, a value of an enumeration or an address.
  bool plain(const Type* ty) noexcept;

  // Every value `ty` is made of in memory, with where each starts: C's
  // flattening, an array's elements among them.
  std::vector<std::pair<std::uint64_t, const Type*>> scalars_of(const Type* ty, std::uint64_t at);

  // How C answers `record` on `triple`: the registers in order, or nothing
  // where it is answered through room the caller makes.
  std::optional<std::vector<Piece>> answered_by_c(std::string_view triple, const ProductType* record);

} // namespace pl4g::ir::cabi
