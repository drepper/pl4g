// What a container owns, copied into its allocator and given back from it.
//
// A port of pypl4g's sema/owned.py.  A container's elements point into the
// container's allocator; an element whose making the compiler cannot prove was
// there is copied in on the way, by a function per type generated on first
// ask, and what is replaced is given back.
#pragma once

#include <optional>
#include <string_view>

#include "ir/builder.hh"
#include "ir/ir.hh"


namespace pl4g::sema::owned {

  inline constexpr std::string_view own_symbol = "__pl4g_own";
  inline constexpr std::string_view disown_symbol = "__pl4g_disown";

  // Whether a value of `ty` may point into room something else holds -- a
  // lambda only with `lambdas`, a reference only with `references`.  Ports
  // pypl4g's `check._points_somewhere`, which the checker uses too.
  bool points_somewhere(const ir::Type* ty, bool lambdas = true, bool references = true);

  // Whether a value of `ty` can be copied into an allocator and given back.
  bool can_own(const ir::Type* ty);
  // Whether a value of `ty` points into room an allocator gave out.
  bool points(const ir::Type* ty);

  // `__pl4g_own.T(arena, v)`: `v` copied into `arena`, all the way down.
  ir::Function* own_function(ir::Module& module, const ir::Type* ty);
  // `__pl4g_disown.T(v)`: `v` given back, all the way down.
  ir::Function* disown_function(ir::Module& module, const ir::Type* ty);

  // Give `value` back, using what the compiler knows of its allocator:
  // "pool", "heap", or nothing where it has to be asked of the value.
  void give_back(ir::Builder& builder, ir::Value* value, const ir::Type* ty, std::optional<std::string_view> known);
  // Give back a string's bytes or a list's run, and nothing they point at.
  void give_back_storage(ir::Builder& builder, ir::Value* value, const ir::Type* ty,
                         std::optional<std::string_view> known);

} // namespace pl4g::sema::owned
