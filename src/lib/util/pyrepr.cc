// Text written as Python's `repr` writes a string.
#include "util/pyrepr.hh"

#include <unictype.h>

#include <format>

#include "util/unicode.hh"


namespace pl4g {

  bool python_printable(char32_t cp) noexcept
  {
    if (cp == U' ')
      return true;
    // What Python leaves out: the controls, the format characters, the
    // surrogates, the private uses, what is unassigned, and every separator
    // but the space.
    static const ::uc_general_category_t unprinted = ::uc_general_category_or(
      ::uc_general_category_or(::uc_general_category_or(UC_CATEGORY_Cc, UC_CATEGORY_Cf),
                               ::uc_general_category_or(UC_CATEGORY_Cs, UC_CATEGORY_Co)),
      ::uc_general_category_or(::uc_general_category_or(UC_CATEGORY_Cn, UC_CATEGORY_Zl),
                               ::uc_general_category_or(UC_CATEGORY_Zp, UC_CATEGORY_Zs)));
    return ! ::uc_is_general_category(::ucs4_t(cp), unprinted);
  }


  std::string python_str_repr(std::string_view s)
  {
    char quote = s.contains('\'') && ! s.contains('"') ? '"' : '\'';
    std::string out(1, quote);
    for (std::size_t at = 0; at < s.size();) {
      auto d = unicode::decode(s, at);
      at += d.length > 0 ? d.length : 1;
      char32_t cp = d.cp;
      if (cp == U'\\' || cp == char32_t(quote)) {
        out += '\\';
        out += char(cp);
      } else if (cp == U'\n')
        out += "\\n";
      else if (cp == U'\r')
        out += "\\r";
      else if (cp == U'\t')
        out += "\\t";
      else if (cp < 0x80 && cp >= 0x20 && cp != 0x7f)
        out += char(cp);
      else if (python_printable(cp))
        unicode::append(out, cp);
      else if (cp < 0x100)
        out += std::format("\\x{:02x}", unsigned(cp));
      else if (cp < 0x10000)
        out += std::format("\\u{:04x}", unsigned(cp));
      else
        out += std::format("\\U{:08x}", unsigned(cp));
    }
    out += quote;
    return out;
  }

} // namespace pl4g
