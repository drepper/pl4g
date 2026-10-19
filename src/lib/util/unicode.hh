// UTF-8 and the character classes the language asks Unicode about.
//
// Source text is kept as UTF-8 bytes, checked once when a file is read, so
// everything after that may decode without checking again.  What a character
// *is* -- a letter, a digit, a symbol -- is libunistring's answer, from the same
// Unicode database Python's `unicodedata` answers from (one version older: the
// installed 1.1 has Unicode 15, Python 16, which differ only in the letters of
// the scripts 16 added).
#pragma once

#include <cstddef>
#include <optional>
#include <string>
#include <string_view>


namespace pl4g::unicode {

  // What a decoder returns past the end of the text.  Not a character: a NUL
  // byte in a file is a character like any other and must not end it.
  inline constexpr char32_t end_of_text = char32_t(-1);

  // One decoded character and how many bytes it took.
  struct Decoded {
    char32_t cp = end_of_text;
    unsigned length = 0;
  };

  // Decode the character at byte offset `at` of text known to be valid UTF-8.
  Decoded decode(std::string_view text, std::size_t at) noexcept;

  // The offset of the first byte that is not valid UTF-8, if there is one.
  // Valid means what Python's strict decoder accepts: no overlong forms, no
  // surrogates, nothing beyond U+10FFFF.
  std::optional<std::size_t> first_invalid(std::string_view text) noexcept;

  // Append the UTF-8 form of `cp` to `out`.
  void append(std::string& out, char32_t cp);

  // The number of characters in valid UTF-8 text.
  std::size_t length(std::string_view text) noexcept;

  // Python's `str.isalpha` for one character: the letter categories.
  bool is_alpha(char32_t cp) noexcept;

  // Python's `str.isalnum` for one character: a letter, or anything with a
  // numeric value.
  bool is_alnum(char32_t cp) noexcept;

  // Python's `str.isdigit` for one character: a decimal digit or a digit.
  bool is_digit(char32_t cp) noexcept;

  // Python's `str.isspace` for one character: a space separator, or what the
  // bidirectional algorithm treats as white space or a separator.
  bool is_space(char32_t cp) noexcept;

  // The general categories the lexer asks about.
  bool is_symbol(char32_t cp) noexcept;            // Sm or So
  bool is_open_punctuation(char32_t cp) noexcept;  // Ps
  bool is_close_punctuation(char32_t cp) noexcept; // Pe

  // Whether `cp` matches `\w` in a Python regular expression over text.
  inline bool is_word(char32_t cp) noexcept { return cp == U'_' || is_alnum(cp); }

} // namespace pl4g::unicode
