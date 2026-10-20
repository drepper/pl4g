// Text written as Python's `repr` writes a string, for the outputs of
// pypl4g's that are Python's own representations: the token and tree dumps.
#pragma once

#include <string>
#include <string_view>


namespace pl4g {

  // `repr(s)` of the UTF-8 text `s`: single quotes unless the text holds one
  // and no double quote, a backslash before the quote and itself, `\n`, `\r`
  // and `\t`, and `\x`, `\u` or `\U` for every character Python does not
  // print as it is.
  std::string python_str_repr(std::string_view s);

  // Whether Python's `str.isprintable` holds of the one character `cp`.
  bool python_printable(char32_t cp) noexcept;

} // namespace pl4g
