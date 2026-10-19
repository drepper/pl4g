#include "util/unicode.hh"

#include <unictype.h>
#include <unistr.h>


namespace pl4g::unicode {

  Decoded decode(std::string_view text, std::size_t at) noexcept
  {
    if (at >= text.size())
      return {};
    ::ucs4_t cp;
    int n = ::u8_mbtouc_unsafe(&cp, reinterpret_cast<const ::uint8_t*>(text.data() + at), text.size() - at);
    return { char32_t(cp), unsigned(n) };
  }


  std::optional<std::size_t> first_invalid(std::string_view text) noexcept
  {
    // libunistring holds to what Python's strict decoder does: no overlong
    // forms, no surrogates, nothing beyond U+10FFFF.
    auto bytes = reinterpret_cast<const ::uint8_t*>(text.data());
    auto bad = ::u8_check(bytes, text.size());
    if (bad == nullptr)
      return std::nullopt;
    return std::size_t(bad - bytes);
  }


  void append(std::string& out, char32_t cp)
  {
    ::uint8_t buf[6];
    int n = ::u8_uctomb(buf, ::ucs4_t(cp), sizeof(buf));
    if (n > 0) [[likely]]
      out.append(reinterpret_cast<const char*>(buf), std::size_t(n));
  }


  std::size_t length(std::string_view text) noexcept
  {
    std::size_t n = 0;
    for (char c : text)
      n += (static_cast<unsigned char>(c) & 0xc0) != 0x80;
    return n;
  }


  bool is_alpha(char32_t cp) noexcept
  {
    return cp != end_of_text && ::uc_is_general_category(::ucs4_t(cp), UC_CATEGORY_L);
  }


  bool is_alnum(char32_t cp) noexcept
  {
    // Python's rule: a letter, or anything with a numeric value at all.
    return is_alpha(cp) || (cp != end_of_text && ::uc_numeric_value(::ucs4_t(cp)).denominator != 0);
  }


  bool is_digit(char32_t cp) noexcept
  {
    return cp != end_of_text && ::uc_digit_value(::ucs4_t(cp)) >= 0;
  }


  bool is_symbol(char32_t cp) noexcept
  {
    return cp != end_of_text && ::uc_is_general_category(::ucs4_t(cp), ::uc_general_category_or(UC_CATEGORY_Sm, UC_CATEGORY_So));
  }


  bool is_open_punctuation(char32_t cp) noexcept
  {
    return cp != end_of_text && ::uc_is_general_category(::ucs4_t(cp), UC_CATEGORY_Ps);
  }


  bool is_close_punctuation(char32_t cp) noexcept
  {
    return cp != end_of_text && ::uc_is_general_category(::ucs4_t(cp), UC_CATEGORY_Pe);
  }

} // namespace pl4g::unicode
