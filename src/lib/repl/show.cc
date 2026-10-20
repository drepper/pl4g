// What the interactive session shows of a value: written as the language
// writes a literal of it where it has one, from the bytes the value is in.
#include "repl/show.hh"

#include <bit>
#include <cstring>
#include <format>

#include "util/pyfloat.hh"
#include "util/unicode.hh"


namespace pl4g::repl {

  namespace {

    // How many elements of a run are shown before the rest is left out.
    constexpr std::size_t shown_elements = 64;
    // How deep a value is followed into what it holds.
    constexpr int shown_depth = 8;

    std::uint64_t word(const std::byte* at, std::size_t bytes)
    {
      std::uint64_t value = 0;
      std::memcpy(&value, at, std::min<std::size_t>(bytes, sizeof value));
      return value;
    }

    // A character as a literal would escape it, inside quotes `quote`.
    void escaped(std::string& out, char32_t cp, char quote)
    {
      switch (cp) {
      case U'\n': out += "\\n"; return;
      case U'\t': out += "\\t"; return;
      case U'\r': out += "\\r"; return;
      case U'\\': out += "\\\\"; return;
      case U'\0': out += "\\0"; return;
      default: break;
      }
      if (cp == char32_t(quote)) {
        out += '\\';
        out += quote;
        return;
      }
      if (cp < 0x20 || cp == 0x7f) {
        out += std::format("\\x{:02x}", unsigned(cp));
        return;
      }
      unicode::append(out, cp);
    }


    class Shower {
    public:
      explicit Shower(const ir::DataLayout& layout) : layout(layout) {}

      std::string operator()(const ir::Type* ty, const std::byte* at, int depth)
      {
        if (at == nullptr)
          return "\N{SINGLE LEFT-POINTING ANGLE QUOTATION MARK}nowhere\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}";
        if (depth > shown_depth)
          return "\N{HORIZONTAL ELLIPSIS}";
        switch (ty->kind) {
        case ir::TypeKind::bool_:
          return word(at, 1) != 0 ? "true" : "false";
        case ir::TypeKind::char_: {
          std::string out = "'";
          escaped(out, char32_t(word(at, 4)), '\'');
          return out + "'";
        }
        case ir::TypeKind::int_:
          return integer(static_cast<const ir::IntType*>(ty), at);
        case ir::TypeKind::float_: {
          auto bits = static_cast<const ir::FloatType*>(ty)->bits;
          if (bits == 32)
            return python_repr(double(std::bit_cast<float>(std::uint32_t(word(at, 4)))));
          return python_repr(std::bit_cast<double>(word(at, 8)));
        }
        case ir::TypeKind::enum_: {
          auto e = static_cast<const ir::EnumType*>(ty);
          auto number = std::int64_t(word(at, ir::size_of(e->holder, layout)));
          for (std::size_t i = 0; i < e->values.size(); ++i)
            if (e->values[i] == number)
              return e->name + "." + e->members[i];
          return std::format("{}({})", e->name, number);
        }
        case ir::TypeKind::str: {
          auto bytes = reinterpret_cast<const char*>(word(at, layout.pointer_size));
          auto count = word(at + layout.pointer_size, 8);
          if (bytes == nullptr && count != 0)
            return "\N{SINGLE LEFT-POINTING ANGLE QUOTATION MARK}nowhere\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}";
          std::string_view text(bytes, count);
          std::string out = "\"";
          for (std::size_t i = 0; i < text.size();) {
            auto d = unicode::decode(text, i);
            escaped(out, d.cp, '"');
            i += std::max<std::size_t>(d.length, 1);
          }
          return out + "\"";
        }
        case ir::TypeKind::array:
          return array(static_cast<const ir::ArrayType*>(ty), at, depth);
        case ir::TypeKind::tuple: {
          auto tuple = static_cast<const ir::TupleType*>(ty);
          auto offsets = ir::member_offsets_of(tuple, layout);
          std::string out = "(";
          for (std::size_t i = 0; i < tuple->members.size(); ++i)
            out += (i > 0 ? ", " : "") + (*this)(tuple->members[i], at + offsets[i], depth + 1);
          return out + ")";
        }
        case ir::TypeKind::product: {
          auto record = static_cast<const ir::ProductType*>(ty);
          auto offsets = ir::offsets_of(record, layout);
          std::string out = record->name + "(";
          for (std::size_t i = 0; i < record->fields.size(); ++i)
            out += std::format("{}.{} \N{LEFTWARDS ARROW} {}", i > 0 ? ", " : "", record->fields[i].first,
                               (*this)(record->fields[i].second, at + offsets[i], depth + 1));
          return out + ")";
        }
        case ir::TypeKind::result: {
          auto result = static_cast<const ir::ResultType*>(ty);
          bool failed = word(at + ir::tag_offset_of(result, layout), 1) != 0;
          if (! failed)
            return (*this)(result->ok, at, depth + 1);
          if (result->err == nullptr)
            return "\N{UP TACK}";
          return "\N{UP TACK} " + (*this)(result->err, at + ir::error_offset_of(result, layout), depth + 1);
        }
        case ir::TypeKind::sum: {
          auto sum = static_cast<const ir::SumType*>(ty);
          auto tag = word(at + ir::tag_offset_of(sum, layout), 8);
          auto holder = ir::holder_for(sum->variants.size());
          tag &= holder->bits >= 64 ? ~std::uint64_t(0) : (std::uint64_t(1) << holder->bits) - 1;
          if (tag >= sum->variants.size())
            return "\N{SINGLE LEFT-POINTING ANGLE QUOTATION MARK}" + sum->name + "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}";
          const auto& [name, part] = sum->variants[tag];
          if (part == nullptr || part == ir::void_type())
            return sum->name + "." + name;
          return std::format("{}.{}({})", sum->name, name, (*this)(part, at, depth + 1));
        }
        case ir::TypeKind::ptr: {
          auto ptr = static_cast<const ir::PtrType*>(ty);
          auto to = reinterpret_cast<const std::byte*>(word(at, layout.pointer_size));
          if (to == nullptr)
            return "\N{SINGLE LEFT-POINTING ANGLE QUOTATION MARK}nowhere\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}";
          return "&" + (*this)(ptr->pointee, to, depth + 1);
        }
        default:
          return "\N{SINGLE LEFT-POINTING ANGLE QUOTATION MARK}" + ty->written() + "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}";
        }
      }

    private:
      std::string integer(const ir::IntType* ty, const std::byte* at)
      {
        auto bytes = ir::size_of(ty, layout);
        unsigned __int128 value = 0;
        std::memcpy(&value, at, std::min<std::size_t>(bytes, sizeof value));
        unsigned width = unsigned(bytes) * 8;
        if (width < 128)
          value &= (static_cast<unsigned __int128>(1) << width) - 1;
        if (ty->is_signed && width < 128 && (value >> (width - 1)) != 0)
          return "-" + decimal(((static_cast<unsigned __int128>(1) << width) - value));
        if (ty->is_signed && width == 128 && static_cast<__int128>(value) < 0)
          return "-" + decimal(-value);
        return decimal(value);
      }

      static std::string decimal(unsigned __int128 value)
      {
        if (value == 0)
          return "0";
        std::string out;
        for (; value != 0; value /= 10)
          out.insert(out.begin(), char('0' + int(value % 10)));
        return out;
      }

      // An array: its elements where its type says how many, or where the
      // run it is and how long that is, its two words, say.
      std::string array(const ir::ArrayType* ty, const std::byte* at, int depth)
      {
        const std::byte* first = at;
        std::vector<std::uint64_t> counts;
        for (const auto& one : ty->shape)
          counts.push_back(one.value_or(0));
        if (! ty->fixed()) {
          first = reinterpret_cast<const std::byte*>(word(at, layout.pointer_size));
          for (std::size_t i = 0; i < counts.size(); ++i)
            counts[i] = word(at + layout.pointer_size * (i + 1), 8);
        }
        auto stride = ir::stride_of(ty->element, layout);
        return run(ty->element, first, counts, 0, stride, depth);
      }

      std::string run(const ir::Type* element, const std::byte* at, const std::vector<std::uint64_t>& counts,
                      std::size_t dimension, std::uint64_t stride, int depth)
      {
        std::uint64_t below = 1;
        for (std::size_t i = dimension + 1; i < counts.size(); ++i)
          below *= counts[i];
        std::string out = "\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}";
        for (std::uint64_t i = 0; i < counts[dimension]; ++i) {
          if (i == shown_elements) {
            out += ", \N{HORIZONTAL ELLIPSIS}";
            break;
          }
          if (i > 0)
            out += ", ";
          const std::byte* here = at + i * below * stride;
          out += dimension + 1 < counts.size() ? run(element, here, counts, dimension + 1, stride, depth + 1)
                                               : (*this)(element, here, depth + 1);
        }
        return out + "\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}";
      }

      const ir::DataLayout& layout;
    };

  } // anonymous namespace


  std::string shown(const ir::Type* ty, const std::byte* at, const ir::DataLayout& layout)
  {
    return Shower(layout)(ty, at, 0);
  }

} // namespace pl4g::repl
