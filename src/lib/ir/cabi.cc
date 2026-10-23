#include "ir/cabi.hh"

#include <algorithm>
#include <array>


namespace pl4g::ir::cabi {

  namespace {

    // What each architecture calls the system's convention besides `cdecl`.
    struct SystemNames {
      std::string_view architecture;
      std::string_view own;
    };
    constexpr std::array<SystemNames, 3> system_names{ { { "x86_64", "sysv64" },
                                                         { "aarch64", "aapcs64" },
                                                         { "riscv64", "lp64" } } };

    // The record cut into words of eight bytes, a register each; `classed`
    // makes a word of nothing but floating-point numbers one of those
    // registers, as x86-64 has it.
    std::vector<Piece> words(const std::vector<std::pair<std::uint64_t, const Type*>>& scalars, std::uint64_t size,
                             bool classed)
    {
      std::vector<Piece> found;
      for (std::uint64_t start = 0; start < size; start += 8) {
        auto width = std::min<std::uint64_t>(8, size - start);
        std::vector<std::pair<std::uint64_t, const Type*>> inside;
        for (const auto& one : scalars)
          if (start <= one.first && one.first < start + 8)
            inside.push_back(one);
        if (classed && ! inside.empty()
            && std::ranges::all_of(inside, [](const auto& one) { return is<FloatType>(one.second); })) {
          found.push_back({ inside.size() == 1 ? inside[0].second : f64(), start, width });
          continue;
        }
        if (inside.size() == 1 && inside[0].first == start && plain(inside[0].second)
            && ! is<FloatType>(inside[0].second)) {
          found.push_back({ inside[0].second, start, width });
          continue;
        }
        found.push_back({ u64(), start, width });
      }
      return found;
    }

    // Each scalar as a register of its own.
    std::vector<Piece> each(const std::vector<std::pair<std::uint64_t, const Type*>>& scalars)
    {
      std::vector<Piece> found;
      for (const auto& [at, one] : scalars)
        found.push_back({ one, at, size_of(one, system_layout) });
      return found;
    }

  } // anonymous namespace


  std::string_view architecture(std::string_view triple) noexcept
  {
    return triple.substr(0, triple.find('-'));
  }


  bool follows_system(std::string_view triple, std::string_view cconv) noexcept
  {
    auto arch = architecture(triple);
    auto found = std::ranges::find(system_names, arch, &SystemNames::architecture);
    return found != system_names.end() && (cconv == system_cconv || cconv == found->own);
  }


  bool answers_its_place(std::string_view triple) noexcept
  {
    return architecture(triple) == "x86_64";
  }


  bool plain(const Type* ty) noexcept
  {
    return is<IntType>(ty) || is<FloatType>(ty) || is<BoolType>(ty) || is<CharType>(ty) || is<EnumType>(ty)
           || is<PtrType>(ty);
  }


  std::vector<std::pair<std::uint64_t, const Type*>> scalars_of(const Type* ty, std::uint64_t at)
  {
    std::vector<std::pair<std::uint64_t, const Type*>> found;
    if (auto record = as<ProductType>(ty)) {
      auto starts = offsets_of(record, system_layout);
      for (std::size_t i = 0; i < record->fields.size(); ++i)
        std::ranges::copy(scalars_of(record->fields[i].second, at + starts[i]), std::back_inserter(found));
      return found;
    }
    if (auto tuple = as<TupleType>(ty)) {
      auto starts = member_offsets_of(tuple, system_layout);
      for (std::size_t i = 0; i < tuple->members.size(); ++i)
        std::ranges::copy(scalars_of(tuple->members[i], at + starts[i]), std::back_inserter(found));
      return found;
    }
    if (auto array = as<ArrayType>(ty); array != nullptr && array->fixed()) {
      auto stride = stride_of(array->element, system_layout);
      for (std::uint64_t index = 0; index < array->count().value_or(0); ++index)
        std::ranges::copy(scalars_of(array->element, at + index * stride), std::back_inserter(found));
      return found;
    }
    if (plain(ty))
      return { { at, ty } };
    auto pieces = parts_of(ty);
    if (pieces.size() != 1 || pieces[0] != ty) {
      auto starts = part_offsets_of(ty, system_layout);
      for (std::size_t i = 0; i < pieces.size(); ++i)
        found.emplace_back(at + starts[i], pieces[i]);
      return found;
    }
    // Bytes, a sum: nothing C puts in a floating-point register.
    return { { at, u8() } };
  }


  std::optional<std::vector<Piece>> answered_by_c(std::string_view triple, const ProductType* record)
  {
    auto size = size_of(record, system_layout);
    auto scalars = scalars_of(record, 0);
    auto arch = architecture(triple);
    if (arch == "aarch64") {
      // A unit is no part of what C sees: all of one width is all alike.
      auto kind = scalars.empty() ? nullptr : as<FloatType>(scalars[0].second);
      if (kind != nullptr && scalars.size() <= 4 && std::ranges::all_of(scalars, [&](const auto& one) {
            auto f = as<FloatType>(one.second);
            return f != nullptr && f->bits == kind->bits;
          }))
        return each(scalars);
      if (size <= in_registers)
        return words(scalars, size, false);
      return std::nullopt;
    }
    if (arch == "riscv64") {
      auto floats = std::ranges::count_if(scalars, [](const auto& one) { return is<FloatType>(one.second); });
      if (floats > 0 && (scalars.size() == 1 || scalars.size() == 2)
          && std::ranges::all_of(scalars, [](const auto& one) { return plain(one.second); }))
        return each(scalars);
      if (size <= in_registers)
        return words(scalars, size, false);
      return std::nullopt;
    }
    if (size <= in_registers)
      return words(scalars, size, true);
    return std::nullopt;
  }

} // namespace pl4g::ir::cabi
