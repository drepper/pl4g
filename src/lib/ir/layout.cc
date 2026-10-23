#include "ir/layout.hh"

#include <algorithm>
#include <bit>
#include <cmath>
#include <limits>
#include <string>

#include "diag/engine.hh"
#include "ir/printer.hh"


namespace pl4g::ir {

  namespace {

    // A tag takes one byte.
    constexpr std::uint64_t tag_size = 1;

    std::uint64_t align_up(std::uint64_t value, std::uint64_t alignment)
    {
      if (alignment <= 1)
        return value;
      auto remainder = value % alignment;
      return remainder == 0 ? value : value + alignment - remainder;
    }

    // The layout to ask with: the system's, where `ty` is a record the system
    // lays out.
    DataLayout as_the_system_would(const Type* ty, const DataLayout& layout)
    {
      auto product = as<ProductType>(ty);
      if (layout.system || product == nullptr || ! product->abi)
        return layout;
      DataLayout system = layout;
      system.system = true;
      return system;
    }

    template<typename F>
    std::uint64_t laid_out_in_a_row(const std::vector<const Type*>& members, const DataLayout& layout, F&& each)
    {
      std::uint64_t total = 0;
      for (auto member : members) {
        total = align_up(total, align_of(member, layout));
        each(total);
        total += size_of(member, layout);
      }
      return total;
    }

    std::vector<const Type*> fields_of(const ProductType* product)
    {
      std::vector<const Type*> out;
      for (const auto& [_, field] : product->fields)
        out.push_back(field);
      return out;
    }

  } // anonymous namespace


  std::uint64_t size_of(const Type* ty, const DataLayout& given)
  {
    auto layout = as_the_system_would(ty, given);
    auto ptr = layout.pointer_size;
    switch (ty->kind) {
    case TypeKind::int_:
      return static_cast<const IntType*>(ty)->held() / 8;
    case TypeKind::float_:
      return static_cast<const FloatType*>(ty)->bits / 8;
    case TypeKind::bool_:
      return 1;
    case TypeKind::char_:
      return size_of(static_cast<const CharType*>(ty)->holder(), layout);
    case TypeKind::syntax:
      return size_of(static_cast<const SyntaxType*>(ty)->holder(), layout);
    case TypeKind::enum_:
      return size_of(static_cast<const EnumType*>(ty)->holder, layout);
    case TypeKind::frame: {
      auto frame = static_cast<const CoroutineFrame*>(ty);
      if (layout.frames != nullptr)
        if (auto it = layout.frames->find(frame); it != layout.frames->end())
          return it->second;
      throw NoLayout{ ty };
    }
    case TypeKind::running:
      return 4 * ptr;
    case TypeKind::func:
      // Where the code is and nothing else for one of the system's convention,
      // as C's pointer to a function; the code, what was brought in and its
      // allocator otherwise.
      return static_cast<const FuncType*>(ty)->system() ? ptr : 3 * ptr;
    case TypeKind::iter:
      return 3 * ptr;
    case TypeKind::opaque:
      return ptr;
    case TypeKind::tuple: {
      auto tuple = static_cast<const TupleType*>(ty);
      auto total = laid_out_in_a_row(tuple->members, layout, [](auto) {});
      return align_up(total, align_of(ty, layout));
    }
    case TypeKind::vec: {
      auto vec = static_cast<const VecType*>(ty);
      return vec->lanes * stride_of(vec->element, layout);
    }
    case TypeKind::array: {
      auto array = static_cast<const ArrayType*>(ty);
      if (auto count = array->count())
        return *count * stride_of(array->element, layout);
      return (1 + 2 * array->rank()) * ptr;
    }
    case TypeKind::list:
    case TypeKind::str:
      return 3 * ptr;
    case TypeKind::cursor:
      return 2 * ptr;
    case TypeKind::set:
    case TypeKind::dict:
    case TypeKind::ptr:
      return ptr;
    case TypeKind::void_:
      return 0;
    case TypeKind::product: {
      auto total = laid_out_in_a_row(fields_of(static_cast<const ProductType*>(ty)), layout, [](auto) {});
      return align_up(total, align_of(ty, layout));
    }
    case TypeKind::sum: {
      std::uint64_t payload = 0;
      for (const auto& [_, v] : static_cast<const SumType*>(ty)->variants)
        payload = std::max(payload, size_of(v, layout));
      return align_up(payload + tag_size, align_of(ty, layout));
    }
    case TypeKind::result: {
      auto result = static_cast<const ResultType*>(ty);
      auto total = size_of(result->ok, layout) + tag_size;
      if (result->err != nullptr)
        total = align_up(total, align_of(result->err, layout)) + size_of(result->err, layout);
      return align_up(total, align_of(ty, layout));
    }
    default:
      throw NoLayout{ ty };
    }
  }


  std::uint64_t align_of(const Type* ty, const DataLayout& given)
  {
    auto layout = as_the_system_would(ty, given);
    auto ptr = layout.pointer_size;
    switch (ty->kind) {
    case TypeKind::int_:
      return static_cast<const IntType*>(ty)->held() / 8;
    case TypeKind::float_:
      return static_cast<const FloatType*>(ty)->bits / 8;
    case TypeKind::bool_:
      return 1;
    case TypeKind::char_:
      return align_of(static_cast<const CharType*>(ty)->holder(), layout);
    case TypeKind::syntax:
      return align_of(static_cast<const SyntaxType*>(ty)->holder(), layout);
    case TypeKind::enum_:
      return align_of(static_cast<const EnumType*>(ty)->holder, layout);
    case TypeKind::frame:
      return 16;
    case TypeKind::func:
    case TypeKind::iter:
    case TypeKind::opaque:
    case TypeKind::running:
      return ptr;
    case TypeKind::tuple: {
      std::uint64_t found = 1;
      for (auto m : static_cast<const TupleType*>(ty)->members)
        found = std::max(found, align_of(m, layout));
      return found;
    }
    case TypeKind::vec:
      return align_of(static_cast<const VecType*>(ty)->element, layout);
    case TypeKind::array: {
      auto array = static_cast<const ArrayType*>(ty);
      if (array->fixed()) {
        auto plain = align_of(array->element, layout);
        if (layout.system || size_of(ty, layout) < wide_enough)
          return plain;
        return std::max(plain, wide_enough);
      }
      return ptr;
    }
    case TypeKind::list:
    case TypeKind::str:
    case TypeKind::cursor:
    case TypeKind::set:
    case TypeKind::dict:
    case TypeKind::ptr:
      return ptr;
    case TypeKind::void_:
      return 1;
    case TypeKind::product: {
      std::uint64_t found = 1;
      for (const auto& [_, f] : static_cast<const ProductType*>(ty)->fields)
        found = std::max(found, align_of(f, layout));
      return found;
    }
    case TypeKind::sum: {
      std::uint64_t found = 1;
      for (const auto& [_, v] : static_cast<const SumType*>(ty)->variants)
        found = std::max(found, align_of(v, layout));
      return found;
    }
    case TypeKind::result: {
      auto result = static_cast<const ResultType*>(ty);
      auto found = align_of(result->ok, layout);
      return result->err == nullptr ? found : std::max(found, align_of(result->err, layout));
    }
    default:
      throw NoLayout{ ty };
    }
  }


  std::uint64_t stride_of(const Type* ty, const DataLayout& layout)
  {
    return align_up(size_of(ty, layout), align_of(ty, layout));
  }


  std::vector<std::uint64_t> offsets_of(const ProductType* ty, const DataLayout& given)
  {
    auto layout = as_the_system_would(ty, given);
    std::vector<std::uint64_t> found;
    laid_out_in_a_row(fields_of(ty), layout, [&](auto at) { found.push_back(at); });
    return found;
  }


  std::vector<std::uint64_t> member_offsets_of(const TupleType* ty, const DataLayout& layout)
  {
    std::vector<std::uint64_t> found;
    laid_out_in_a_row(ty->members, layout, [&](auto at) { found.push_back(at); });
    return found;
  }


  std::uint64_t tag_offset_of(const Type* ty, const DataLayout& layout)
  {
    if (auto result = as<ResultType>(ty))
      return size_of(result->ok, layout);
    std::uint64_t found = 0;
    for (const auto& [_, v] : static_cast<const SumType*>(ty)->variants)
      found = std::max(found, size_of(v, layout));
    return found;
  }


  std::uint64_t error_offset_of(const ResultType* ty, const DataLayout& layout)
  {
    return align_up(size_of(ty->ok, layout) + tag_size, align_of(ty->err, layout));
  }


  std::vector<std::uint64_t> part_offsets_of(const Type* ty, const DataLayout& layout)
  {
    if (auto result = as<ResultType>(ty)) {
      if (result->err == nullptr)
        return { 0, tag_offset_of(ty, layout) };
      return { 0, tag_offset_of(ty, layout), error_offset_of(result, layout) };
    }
    if (is<ProductType>(ty) || is<TupleType>(ty)) {
      std::vector<const Type*> held;
      std::vector<std::uint64_t> starts;
      if (auto tuple = as<TupleType>(ty)) {
        held = tuple->members;
        starts = member_offsets_of(tuple, layout);
      } else {
        auto product = static_cast<const ProductType*>(ty);
        held = fields_of(product);
        starts = offsets_of(product, layout);
      }
      std::vector<std::uint64_t> found;
      for (std::size_t i = 0; i < held.size(); ++i) {
        if (parts_of(held[i]).size() > 1) {
          for (auto inner : part_offsets_of(held[i], layout))
            found.push_back(starts[i] + inner);
          continue;
        }
        found.push_back(starts[i]);
      }
      return found;
    }
    std::vector<std::uint64_t> spread;
    laid_out_in_a_row(parts_of(ty), layout, [&](auto at) { spread.push_back(at); });
    return spread;
  }


  std::vector<std::uint64_t> leaf_offsets_of(const Type* ty, const DataLayout& layout)
  {
    if (auto result = as<ResultType>(ty)) {
      auto found = leaf_offsets_of(result->ok, layout);
      found.push_back(tag_offset_of(ty, layout));
      if (result->err != nullptr) {
        auto base = error_offset_of(result, layout);
        for (auto inner : leaf_offsets_of(result->err, layout))
          found.push_back(base + inner);
      }
      return found;
    }
    return part_offsets_of(ty, layout);
  }

  std::vector<std::uint8_t> encode_scalar(__int128 value, const Type* ty, const DataLayout& layout)
  {
    auto size = size_of(ty, layout);
    __int128 low = size != 0 ? -(static_cast<__int128>(1) << (size * 8 - 1)) : 0;
    unsigned __int128 high = size >= 16 ? ~static_cast<unsigned __int128>(0)
                             : size != 0 ? (static_cast<unsigned __int128>(1) << (size * 8)) - 1 : 0;
    if (value < low || (value > 0 && static_cast<unsigned __int128>(value) > high)) [[unlikely]]
      throw InternalError(decimal(value) + " does not fit in '" + ty->render() + "'");
    std::vector<std::uint8_t> out(size);
    auto bits = static_cast<unsigned __int128>(value);
    for (std::uint64_t i = 0; i < size; ++i) {
      auto at = layout.little_endian ? i : size - 1 - i;
      out[at] = std::uint8_t(bits >> (8 * i));
    }
    return out;
  }


  std::vector<std::uint8_t> encode_float(double value, const Type* ty, const DataLayout& layout)
  {
    auto size = size_of(ty, layout);
    std::uint64_t bits = 0;
    if (size == 4) {
      if (std::isfinite(value) && std::fabs(value) > double(std::numeric_limits<float>::max())) [[unlikely]]
        throw InternalError("float too large to pack with f format");
      bits = std::bit_cast<std::uint32_t>(static_cast<float>(value));
    } else if (size == 8)
      bits = std::bit_cast<std::uint64_t>(value);
    else [[unlikely]]
      throw InternalError(std::to_string(size) + " does not fit in '" + ty->render() + "'");
    std::vector<std::uint8_t> out(size);
    for (std::uint64_t i = 0; i < size; ++i)
      out[layout.little_endian ? i : size - 1 - i] = std::uint8_t(bits >> (8 * i));
    return out;
  }

} // namespace pl4g::ir
