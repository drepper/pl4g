#include "front/lexer.hh"

#include <algorithm>
#include <array>
#include <charconv>
#include <cmath>
#include <cstdlib>
#include <format>
#include <limits>
#include <tuple>
#include <optional>

#include "util/unicode.hh"


namespace pl4g {

  namespace {

    using unicode::end_of_text;

    // The tokens of one character, and the character each is.
    constexpr auto simple_tokens = std::to_array<std::pair<char32_t, TokKind>>({
      { U'(', TokKind::lparen },
      { U')', TokKind::rparen },
      { U'[', TokKind::lbracket },
      { U']', TokKind::rbracket },
      { U'{', TokKind::lbrace },
      { U'}', TokKind::rbrace },
      { U',', TokKind::comma },
      { U':', TokKind::colon },
      { U';', TokKind::semicolon },
      { U'$', TokKind::dollar },
      { U'=', TokKind::equals },
      { U'\N{RIGHTWARDS ARROW}', TokKind::arrow },
      { U'\N{LEFTWARDS ARROW}', TokKind::assign },
      { U'.', TokKind::dot },
      { U'&', TokKind::ampersand },
      { U'|', TokKind::pipe },
      { U'^', TokKind::caret },
      { U'~', TokKind::tilde },
      { U'\N{NOT EQUAL TO}', TokKind::not_equal },
      { U'<', TokKind::less },
      { U'>', TokKind::greater },
      { U'\N{LESS-THAN OR EQUAL TO}', TokKind::less_equal },
      { U'\N{GREATER-THAN OR EQUAL TO}', TokKind::greater_equal },
      { U'\N{APPROXIMATELY EQUAL TO}', TokKind::alike },
      { U'\N{NEITHER APPROXIMATELY NOR ACTUALLY EQUAL TO}', TokKind::unalike },
      { U'\N{LESS-THAN OR APPROXIMATE}', TokKind::below_or_alike },
      { U'\N{GREATER-THAN OR APPROXIMATE}', TokKind::above_or_alike },
      { U'\N{LESS-THAN AND NOT APPROXIMATE}', TokKind::below_not_alike },
      { U'\N{GREATER-THAN AND NOT APPROXIMATE}', TokKind::above_not_alike },
      { U'\N{LOGICAL AND}', TokKind::logic_and },
      { U'\N{LOGICAL OR}', TokKind::logic_or },
      { U'\N{CIRCLED PLUS}', TokKind::logic_xor },
      { U'\N{NAND}', TokKind::logic_nand },
      { U'\N{NOR}', TokKind::logic_nor },
      { U'\N{NOT SIGN}', TokKind::logic_not },
      { U'\N{DOUBLE PLUS}', TokKind::concat },
      { U'\N{APL FUNCTIONAL SYMBOL RHO}', TokKind::shape },
      { U'\N{LEFT CEILING}', TokKind::max },
      { U'\N{LEFT FLOOR}', TokKind::min },
      { U'\N{DOWNWARDS ARROW}', TokKind::floor },
      { U'\N{UPWARDS ARROW}', TokKind::ceiling },
      { U'\N{UP DOWN ARROW}', TokKind::nearest },
      { U'\N{UP DOWN DOUBLE ARROW}', TokKind::rounded },
      { U'\N{SUPERSCRIPT LATIN SMALL LETTER N}', TokKind::power },
      { U'\N{DIVIDES}', TokKind::divides },
      { U'\N{DOES NOT DIVIDE}', TokKind::not_divides },
      { U'\N{TOP LEFT CORNER}', TokKind::lift_open },
      { U'\N{TOP RIGHT CORNER}', TokKind::lift_close },
      { U'\N{POSITION INDICATOR}', TokKind::deref },
      { U'\N{DIAERESIS}', TokKind::each },
      { U'\N{RIGHTWARDS PAIRED ARROWS}', TokKind::iter },
      { U'\N{LEFTWARDS PAIRED ARROWS}', TokKind::takes },
      { U'\N{CURRENCY SIGN}', TokKind::unit },
      { U'\N{GREEK SMALL LETTER LAMDA}', TokKind::lambda },
      { U'\N{WHITE HOURGLASS}', TokKind::lifetime },
      { U'\N{SQUARED PLUS}', TokKind::sat_add },
      { U'\N{SQUARED MINUS}', TokKind::sat_sub },
      { U'\N{SQUARED TIMES}', TokKind::sat_mul },
      { U'+', TokKind::plus },
      { U'-', TokKind::minus },
      { U'\N{MULTIPLICATION SIGN}', TokKind::times },
      { U'\N{DIVISION SIGN}', TokKind::divide },
      { U'%', TokKind::percent },
      { U'#', TokKind::length },
      { U'\N{LEFT-POINTING DOUBLE ANGLE QUOTATION MARK}', TokKind::shift_left },
      { U'\N{RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK}', TokKind::shift_right },
      { U'\N{ANTICLOCKWISE OPEN CIRCLE ARROW}', TokKind::rotate_left },
      { U'\N{CLOCKWISE OPEN CIRCLE ARROW}', TokKind::rotate_right },
      { U'\N{LEFT ANGLE BRACKET}', TokKind::tuple_open },
      { U'\N{RIGHT ANGLE BRACKET}', TokKind::tuple_close },
      { U'\N{DAGGER}', TokKind::take },
      { U'\N{UPWARDS WHITE ARROW}', TokKind::next },
      { U'\N{DOWNWARDS WHITE ARROW}', TokKind::prev },
      { U'\N{LEFT DOUBLE PARENTHESIS}', TokKind::set_open },
      { U'\N{RIGHT DOUBLE PARENTHESIS}', TokKind::set_close },
      { U'\N{ASTERISM}', TokKind::spread },
      { U'\N{SECTION SIGN}', TokKind::label },
      { U'\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}', TokKind::array_open },
      { U'\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}', TokKind::array_close },
      { U'?', TokKind::question },
      { U'\N{UP TACK}', TokKind::bottom },
      { U'\N{HORIZONTAL ELLIPSIS}', TokKind::range },
    });

    // The token one character is, if it is one by itself.
    std::optional<TokKind> simple_kind(char32_t cp) noexcept
    {
      auto it = std::ranges::find(simple_tokens, cp, &std::pair<char32_t, TokKind>::first);
      return it == simple_tokens.end() ? std::nullopt : std::optional(it->second);
    }

    bool is_open_kind(TokKind k) noexcept
    {
      switch (k) {
      case TokKind::lparen: case TokKind::lbracket: case TokKind::lbrace: case TokKind::at_lbracket:
      case TokKind::set_open: case TokKind::tuple_open: case TokKind::array_open:
        return true;
      default:
        return false;
      }
    }

    bool is_close_kind(TokKind k) noexcept
    {
      switch (k) {
      case TokKind::rparen: case TokKind::rbracket: case TokKind::rbrace: case TokKind::set_close:
      case TokKind::tuple_close: case TokKind::array_close:
        return true;
      default:
        return false;
      }
    }

    // The ASCII spellings of glyphs, which are always more than one character,
    // in the order they are tried.
    constexpr std::array<std::tuple<std::string_view, std::string_view, TokKind>, 4> ascii_substitutes{ {
      { "->", "\N{RIGHTWARDS ARROW}", TokKind::arrow },
      { "<=", "\N{LESS-THAN OR EQUAL TO}", TokKind::less_equal },
      { ">=", "\N{GREATER-THAN OR EQUAL TO}", TokKind::greater_equal },
      { "++", "\N{DOUBLE PLUS}", TokKind::concat },
    } };

    // The glyphs the language uses as operators whatever Unicode calls them.
    constexpr auto operator_glyphs = std::to_array<char32_t>({
      U'-', U'^', U'&', U'%', U'#', U'\N{LEFT CEILING}', U'\N{LEFT FLOOR}',
      U'\N{LEFT-POINTING DOUBLE ANGLE QUOTATION MARK}', U'\N{RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK}',
      U'\N{SUPERSCRIPT LATIN SMALL LETTER N}'
    });

    // The glyphs the grammar needs for itself, which no program may define.
    constexpr auto operators_not_nameable = std::to_array<char32_t>({
      U'\N{LOGICAL AND}', U'\N{LOGICAL OR}', U'?', U'\N{POSITION INDICATOR}', U'\N{DIAERESIS}',
      U'\N{RIGHTWARDS PAIRED ARROWS}', U'\N{LEFTWARDS PAIRED ARROWS}', U'\N{RIGHTWARDS ARROW}',
      U'\N{LEFTWARDS ARROW}', U'\N{UP TACK}', U'\N{APL FUNCTIONAL SYMBOL QUAD}', U'\N{WHITE HOURGLASS}',
      U'\N{TOP LEFT CORNER}', U'\N{TOP RIGHT CORNER}', U'\N{SUPERSCRIPT MINUS}',
      U'(', U')', U'[', U']', U'{', U'}', U'\N{LEFT ANGLE BRACKET}', U'\N{RIGHT ANGLE BRACKET}'
    });

    bool contains(const auto& set, char32_t cp) noexcept
    {
      return std::ranges::find(set, cp) != std::ranges::end(set);
    }

    bool not_nameable(char32_t cp) noexcept
    {
      return contains(operators_not_nameable, cp);
    }

    // The value of a raised digit, or -1.
    int superscript_digit(char32_t cp) noexcept
    {
      switch (cp) {
      case U'\N{SUPERSCRIPT ZERO}': return 0;
      case U'\N{SUPERSCRIPT ONE}': return 1;
      case U'\N{SUPERSCRIPT TWO}': return 2;
      case U'\N{SUPERSCRIPT THREE}': return 3;
      case U'\N{SUPERSCRIPT FOUR}': return 4;
      case U'\N{SUPERSCRIPT FIVE}': return 5;
      case U'\N{SUPERSCRIPT SIX}': return 6;
      case U'\N{SUPERSCRIPT SEVEN}': return 7;
      case U'\N{SUPERSCRIPT EIGHT}': return 8;
      case U'\N{SUPERSCRIPT NINE}': return 9;
      default: return -1;
      }
    }

    // Whether `cp` may begin an identifier.  The quad begins one too, and only
    // a name the compiler provides; the checker refuses it anywhere else, so the
    // message is about the name and not about the character.
    bool is_ident_start(char32_t cp) noexcept
    {
      return unicode::is_alpha(cp) || cp == U'_' || cp == glyph::builtin;
    }

    // Whether `cp` may continue an identifier.  A raised digit does not, nor
    // the raised letter: `a²` is a square and not a name.
    bool is_ident_continue(char32_t cp) noexcept
    {
      return (unicode::is_alnum(cp) || cp == U'_' || cp == U'\'') && superscript_digit(cp) < 0
             && cp != glyph::power;
    }

    // The digits of each radix, underscores included.
    constexpr std::string_view hex_digits = "0123456789abcdefABCDEF_";
    constexpr std::string_view octal_digits = "01234567_";
    constexpr std::string_view binary_digits = "01_";
    constexpr std::string_view decimal_digits = "0123456789_";

    bool in(std::string_view set, char32_t cp) noexcept
    {
      return cp < 0x80 && set.find(char(cp)) != std::string_view::npos;
    }

    char32_t ascii_lower(char32_t cp) noexcept
    {
      return cp >= U'A' && cp <= U'Z' ? cp + 32 : cp;
    }

    // `'c' (U+0063)`, which is how a character is shown in a diagnostic.
    std::string show_char(char32_t cp)
    {
      std::string out = "'";
      unicode::append(out, cp);
      return out += std::format("' (U+{:04X})", unsigned(cp));
    }


    // Turns the text of one source file into tokens.
    class Lexer {
    public:
      Lexer(const SourceFile& source, DiagEngine& diags, bool lazy, std::size_t start, std::size_t end)
        : text(std::string_view(source.text).substr(0, end)), base(source.base), diags(diags), pos(start), lazy(lazy)
      {}

      std::vector<Token> run();

    private:
      Span span(std::size_t start, std::size_t end) const noexcept
      {
        return { base + Loc(start), base + Loc(end) };
      }

      // The character `ahead` characters from the cursor, or end_of_text.
      char32_t peek(unsigned ahead = 0) const noexcept
      {
        std::size_t at = pos;
        for (; ahead > 0 && at < text.size(); --ahead)
          at += unicode::decode(text, at).length;
        return unicode::decode(text, at).cp;
      }

      // Step over one character.
      void advance() noexcept { pos += std::max(1u, unicode::decode(text, pos).length); }

      bool starts_with(std::string_view what, std::size_t at) const noexcept
      {
        return text.substr(std::min(at, text.size())).starts_with(what);
      }

      // Append a token from `start` to the cursor.
      Token& emit(TokKind kind, std::size_t start)
      {
        auto& t = tokens.emplace_back();
        t.kind = kind;
        t.span = span(start, pos);
        t.text = text.substr(start, pos - start);
        return t;
      }

      // Append a token with no text.
      void plain(TokKind kind, std::size_t start, std::size_t end)
      {
        auto& t = tokens.emplace_back();
        t.kind = kind;
        t.span = span(start, end);
      }

      bool last_is(TokKind kind) const noexcept { return ! tokens.empty() && tokens.back().kind == kind; }

      std::vector<Token> finish();
      bool handle_line_start();
      bool a_body_begins() const noexcept;
      bool set_aside();
      bool continues_a_header() const noexcept;
      void skip_to_end_of_line() noexcept;
      bool lex_one();
      void lex_operator_name(std::size_t start);
      void lex_comment();
      bool blank_line_ended() const noexcept;
      void lex_identifier(std::size_t start);
      void lex_exponent(std::size_t start);
      void lex_number(std::size_t start);
      bool begins_a_fraction(int radix) const noexcept;
      bool digit_follows(unsigned offset, int radix) const noexcept;
      void lex_fraction(std::size_t start, std::size_t digits_start, int radix, bool negative);
      std::string_view lex_literal_suffix(bool floating, bool either);
      void lex_string(std::size_t start);
      void lex_character(std::size_t start);
      // The text of one escape sequence; `nothing` where it denoted nothing.
      std::string lex_escape();

      std::string_view text;
      Loc base;
      DiagEngine& diags;
      std::size_t pos;
      std::vector<Token> tokens;
      std::vector<unsigned> indents{ 0 };
      unsigned bracket_depth = 0;
      bool at_line_start = true;
      // Whether an indented function body is left as text, and whether the
      // definition being read is a function's.
      bool lazy;
      bool top_is_a_function = false;
    };


    std::vector<Token> Lexer::run()
    {
      while (pos < text.size()) {
        if (at_line_start && bracket_depth == 0) {
          if (lazy && a_body_begins() && set_aside())
            continue;
          if (! handle_line_start())
            continue;
        }
        if (! lex_one())
          break;
      }
      return finish();
    }


    std::vector<Token> Lexer::finish()
    {
      auto end = text.size();
      if (bracket_depth == 0 && ! tokens.empty() && ! last_is(TokKind::newline) && ! last_is(TokKind::dedent)
          && ! last_is(TokKind::body))
        plain(TokKind::newline, end, end);
      for (; indents.size() > 1; indents.pop_back())
        plain(TokKind::dedent, end, end);
      plain(TokKind::eof, end, end);
      return std::move(tokens);
    }


    bool Lexer::handle_line_start()
    {
      auto start = pos;
      unsigned width = 0;
      while (true) {
        char32_t ch = peek();
        if (ch == U' ') {
          ++width;
          ++pos;
        } else if (ch == U'\t') {
          diags.emit(D::LANG_SYNTAX_TAB_INDENT, span(pos, pos + 1));
          ++pos;
        } else
          break;
      }
      char32_t ch = peek();
      bool is_comment = starts_with(glyph::comment, pos);
      bool is_doc = starts_with(glyph::doc_comment, pos);
      if (ch == end_of_text || ch == U'\n' || (is_comment && ! is_doc)) {
        // A blank line and a plain comment carry no layout.  A blank line is
        // noted on the end of line before it, the one place it means anything:
        // after an attribute list at the top level.
        if (ch == U'\n' && ! is_comment && last_is(TokKind::newline) && tokens.back().text.empty())
          tokens.back().text = blank_line;
        skip_to_end_of_line();
        return false;
      }
      at_line_start = false;
      if (width == 0)
        top_is_a_function = false;
      if (width > indents.back() && last_is(TokKind::newline) && continues_a_header()) {
        // A function's clauses, and what its body begins with, may stand on
        // lines of their own below its first: such a line goes on with it.
        tokens.pop_back();
        return true;
      }
      if (width > indents.back()) {
        indents.push_back(width);
        plain(TokKind::indent, start, pos);
      } else if (width < indents.back()) {
        while (indents.size() > 1 && width < indents.back()) {
          indents.pop_back();
          plain(TokKind::dedent, start, pos);
        }
        if (width != indents.back()) [[unlikely]] {
          diags.emit(D::LANG_SYNTAX_INCONSISTENT_INDENT, span(start, pos));
          indents.push_back(width);
        }
      }
      return true;
    }


    bool Lexer::a_body_begins() const noexcept
    {
      auto n = tokens.size();
      return top_is_a_function && indents.size() == 1 && n >= 2 && tokens[n - 1].kind == TokKind::newline
             && tokens[n - 2].kind == TokKind::colon && starts_with(" ", pos);
    }


    // How much deeper in brackets a line of a body leaves the next one.  Text,
    // a character written out and a comment are taken out first.
    int depth_change(std::string_view line)
    {
      int change = 0;
      std::size_t at = 0;
      char32_t before = end_of_text;
      while (at < line.size()) {
        auto d = unicode::decode(line, at);
        char32_t cp = d.cp;
        if (cp == U'"') {
          // "(?:[^"\\\n]|\\.)*"
          std::size_t j = at + 1;
          bool matched = false;
          while (j < line.size()) {
            if (line[j] == '"') {
              matched = true;
              ++j;
              break;
            }
            if (line[j] == '\\') {
              if (j + 1 >= line.size())
                break;
              j += 1 + unicode::decode(line, j + 1).length;
            } else
              j += unicode::decode(line, j).length;
          }
          if (matched) {
            at = j;
            before = U'"';
            continue;
          }
        } else if (cp == U'\'' && ! unicode::is_word(before) && before != U'\'') {
          // (?<![\w'])'(?:\\[^'\n]*|[^'\\\n])'
          std::size_t j = at + 1;
          std::optional<std::size_t> end;
          if (j < line.size() && line[j] == '\\') {
            auto close = line.find('\'', j + 1);
            if (close != std::string_view::npos)
              end = close + 1;
          } else if (j < line.size()) {
            auto inner = unicode::decode(line, j);
            auto k = j + inner.length;
            if (inner.cp != U'\'' && k < line.size() && line[k] == '\'')
              end = k + 1;
          }
          if (end) {
            at = *end;
            before = U'\'';
            continue;
          }
        } else if (line.substr(at).starts_with(glyph::comment))
          break;
        if (cp == U'(' || cp == U'[' || cp == U'{')
          ++change;
        else if (cp == U')' || cp == U']' || cp == U'}')
          --change;
        else if (cp >= 0x80) {
          // In the order the lexer asks: a glyph of the language is what it
          // is, and only one the language does not have may be a pair's.
          if (auto kind = simple_kind(cp))
            change += int(is_open_kind(*kind)) - int(is_close_kind(*kind));
          else if (is_operator_opener(cp))
            ++change;
          else if (is_operator_closer(cp))
            --change;
        }
        before = cp;
        at += d.length;
      }
      return change;
    }


    bool Lexer::set_aside()
    {
      // Where the body ends is found a line at a time with no token made: at the
      // first line at the margin that is neither blank nor a comment, comments
      // at the margin just before it being the next definition's.  A body whose
      // brackets do not come out even is lexed as it always was.
      auto start = pos;
      auto at = pos;
      int depth = 0;
      auto end = text.size();
      std::optional<std::size_t> margin_comment;
      bool stopped = false;
      while (at < text.size()) {
        auto newline = text.find('\n', at);
        auto stop = newline == std::string_view::npos ? text.size() : newline;
        auto line = text.substr(at, stop - at);
        auto content = line.substr(std::min(line.find_first_not_of(" \t"), line.size()));
        if (content.empty() || content.starts_with(glyph::comment)) {
          if (! content.empty() && content.size() == line.size() && ! margin_comment)
            margin_comment = at;
          at = stop + 1;
          continue;
        }
        if (content.size() == line.size()) {
          end = margin_comment ? *margin_comment : at;
          stopped = true;
          break;
        }
        margin_comment.reset();
        depth += depth_change(content);
        at = stop + 1;
      }
      if (! stopped)
        end = margin_comment ? *margin_comment : text.size();
      if (depth != 0 || end <= start)
        return false;
      plain(TokKind::body, start, end);
      pos = end;
      at_line_start = true;
      return true;
    }


    bool Lexer::continues_a_header() const noexcept
    {
      // What the six characters from here are, which is all Python looks at.
      std::size_t at = pos;
      for (unsigned i = 0; i < 6 && at < text.size(); ++i)
        at += unicode::decode(text, at).length;
      auto rest = text.substr(pos, at - pos);
      for (std::string_view word : { "pre", "post" })
        if (rest.starts_with(word)) {
          auto after = rest.substr(word.size());
          after.remove_prefix(std::min(after.find_first_not_of(' '), after.size()));
          if (after.starts_with('('))
            return true;
        }
      return rest.starts_with(':') || rest.starts_with('{');
    }


    void Lexer::skip_to_end_of_line() noexcept
    {
      auto index = text.find('\n', pos);
      pos = index == std::string_view::npos ? text.size() : index + 1;
      at_line_start = true;
    }


    bool Lexer::lex_one()
    {
      while (true) {
        char32_t ch = peek();
        if (ch == end_of_text)
          return false;
        if (ch == U' ' || ch == U'\r' || ch == U'\t') {
          ++pos;
          continue;
        }
        if (ch == U'\n') {
          ++pos;
          if (bracket_depth == 0) {
            at_line_start = true;
            if (! tokens.empty() && ! last_is(TokKind::newline))
              plain(TokKind::newline, pos - 1, pos);
            else if (! tokens.empty() && blank_line_ended())
              // Lines with nothing on them are one end of a line, which says
              // there was one: an attribute list before it is attached to
              // nothing that follows.
              tokens.back().text = blank_line;
            return true;
          }
          continue;
        }
        if (starts_with(glyph::comment, pos)) {
          lex_comment();
          if (bracket_depth == 0)
            return true;
          continue;
        }
        break;
      }

      auto start = pos;
      char32_t ch = peek();
      if (ch == U'@' && peek(1) == U'[') {
        pos += 2;
        ++bracket_depth;
        emit(TokKind::at_lbracket, start);
        return true;
      }
      if (ch == U'?' && peek(1) == U'?') {
        // Before the single character, as '<=' comes before '<': the longer
        // reading is the one meant.
        pos += 2;
        emit(TokKind::or_else, start);
        return true;
      }
      for (auto [ascii, glyph_text, kind] : ascii_substitutes) {
        if (! starts_with(ascii, pos))
          continue;
        pos += ascii.size();
        diags.emit(D::LANG_SYNTAX_ASCII_SUBSTITUTE, span(start, pos), { { "ascii", ascii }, { "glyph", glyph_text } });
        emit(kind, start);
        return true;
      }
      if (auto kind = simple_kind(ch)) {
        advance();
        if (is_open_kind(*kind))
          ++bracket_depth;
        else if (is_close_kind(*kind))
          bracket_depth = bracket_depth > 0 ? bracket_depth - 1 : 0;
        emit(*kind, start);
        return true;
      }
      if (is_ident_start(ch)) {
        lex_identifier(start);
        return true;
      }
      if (superscript_digit(ch) >= 0) {
        lex_exponent(start);
        return true;
      }
      if (ch < 0x80 && ch >= U'0' && ch <= U'9') {
        lex_number(start);
        return true;
      }
      if (ch == glyph::negative) {
        // A raised minus begins a negative number where digits follow and a
        // negative exponent where raised digits do.
        if (superscript_digit(peek(1)) >= 0)
          lex_exponent(start);
        else
          lex_number(start);
        return true;
      }
      if (ch == glyph::opname) {
        lex_operator_name(start);
        return true;
      }
      if (ch == U'"') {
        lex_string(start);
        return true;
      }
      if (ch == U'\'') {
        lex_character(start);
        return true;
      }
      if (is_operator_glyph(ch) || is_operator_opener(ch) || is_operator_closer(ch)) {
        // A glyph the language gives no meaning, which a program may have
        // defined as an operator; whether one did is the checker's question.
        // A bracket counts its depth as the grammar's own do.
        TokKind kind = TokKind::op;
        if (is_operator_opener(ch)) {
          kind = TokKind::open_op;
          ++bracket_depth;
        } else if (is_operator_closer(ch)) {
          kind = TokKind::close_op;
          bracket_depth = bracket_depth > 0 ? bracket_depth - 1 : 0;
        }
        advance();
        emit(kind, start);
        return true;
      }
      // Python reads a digit of another script as the start of a number and
      // never moves past it; here it is the character nobody expected.
      advance();
      diags.emit(D::LANG_SYNTAX_UNEXPECTED_CHAR, span(start, pos), { { "char", show_char(ch) } });
      return true;
    }


    void Lexer::lex_operator_name(std::size_t start)
    {
      // One glyph, or a pair, between two grave accents, which cannot run past
      // the end of its line.
      ++pos;
      auto end = text.find('`', pos);
      auto line = text.find('\n', pos);
      if (end == std::string_view::npos || line < end) {
        pos = line == std::string_view::npos ? text.size() : line;
        diags.emit(D::LANG_OPNAME_UNTERMINATED, span(start, pos));
        return;
      }
      auto written = text.substr(pos, end - pos);
      pos = end + 1;
      auto count = unicode::length(written);
      if (count != 1 && count != 2) [[unlikely]] {
        diags.emit(D::LANG_OPNAME_NOT_ONE_GLYPH, span(start, pos), { { "count", count } });
        return;
      }
      if (! is_operator_name(written)) [[unlikely]] {
        diags.emit(D::LANG_OPNAME_NOT_AN_OPERATOR, span(start, pos), { { "glyph", written } });
        return;
      }
      auto& t = tokens.emplace_back();
      t.kind = TokKind::opname;
      t.span = span(start, pos);
      t.text = written;
    }


    void Lexer::lex_comment()
    {
      // A documentation comment is a token; any other is not.
      auto start = pos;
      bool is_doc = starts_with(glyph::doc_comment, pos);
      auto index = text.find('\n', pos);
      auto end = index == std::string_view::npos ? text.size() : index;
      if (is_doc) {
        pos = end;
        emit(TokKind::doc_comment, start);
      }
      pos = index == std::string_view::npos ? text.size() : index + 1;
      if (bracket_depth == 0) {
        at_line_start = true;
        if (! tokens.empty() && ! last_is(TokKind::newline))
          plain(TokKind::newline, end, pos);
      }
    }


    bool Lexer::blank_line_ended() const noexcept
    {
      auto before = pos >= 2 ? text.rfind('\n', pos - 2) : std::string_view::npos;
      auto from = before == std::string_view::npos ? 0 : before + 1;
      auto line = text.substr(from, pos - 1 - from);
      return line.find_first_not_of(" \t\r") == std::string_view::npos;
    }


    void Lexer::lex_identifier(std::size_t start)
    {
      // The first character was only looked at: what may begin a name and
      // what may go on with it are two questions.  A name the compiler
      // provides may carry a key, as `⎕sc@write` does, and only such a name.
      bool provided = peek() == glyph::builtin;
      advance();
      while (is_ident_continue(peek()) || (provided && peek() == glyph::key))
        advance();
      auto kind = keyword_kind(text.substr(start, pos - start));
      if (kind == TokKind::kw_fn && indents.size() == 1 && bracket_depth == 0)
        top_is_a_function = true;
      emit(kind, start);
    }


    void Lexer::lex_exponent(std::size_t start)
    {
      // A raised number is an exponent: no radix and no suffix.  A raised
      // minus is read here and refused where it is checked.
      bool negative = peek() == glyph::negative;
      if (negative)
        advance();
      __int128 value = 0;
      bool too_large = false;
      for (int digit; (digit = superscript_digit(peek())) >= 0; advance()) {
        if (value > (std::numeric_limits<__int128>::max() - digit) / 10)
          too_large = true;
        else
          value = value * 10 + digit;
      }
      auto& t = emit(TokKind::exponent, start);
      t.int_value = negative ? -value : value;
      t.int_too_large = too_large;
    }


    void Lexer::lex_number(std::size_t start)
    {
      // An integer literal, decimal or with a base prefix, and perhaps the name
      // of its type after it.  A leading raised minus makes it negative.
      bool negative = peek() == glyph::negative;
      if (negative) {
        advance();
        char32_t next = peek();
        if (! unicode::is_digit(next)) [[unlikely]] {
          diags.emit(D::LANG_SYNTAX_LONELY_NEGATIVE, span(start, pos));
          emit(TokKind::int_lit, start);
          return;
        }
      }
      int radix = 10;
      std::string_view digits = decimal_digits;
      if (peek() == U'0') {
        switch (ascii_lower(peek(1))) {
        case U'x': radix = 16; digits = hex_digits; pos += 2; break;
        case U'o': radix = 8; digits = octal_digits; pos += 2; break;
        case U'b': radix = 2; digits = binary_digits; pos += 2; break;
        default: break;
        }
      }
      auto digits_start = pos;
      while (in(digits, peek()))
        ++pos;
      if (begins_a_fraction(radix)) {
        lex_fraction(start, digits_start, radix, negative);
        return;
      }
      auto body = text.substr(digits_start, pos - digits_start);
      auto suffix = lex_literal_suffix(false, true);
      unsigned __int128 value = 0;
      bool too_large = false;
      bool any = false;
      for (char c : body) {
        if (c == '_')
          continue;
        any = true;
        unsigned d = c <= '9' ? unsigned(c - '0') : unsigned((c | 0x20) - 'a' + 10);
        if (value > ((unsigned __int128)(std::numeric_limits<__int128>::max()) - d) / unsigned(radix))
          too_large = true;
        else
          value = value * unsigned(radix) + d;
      }
      if (! any) [[unlikely]] {
        // Python's int() of nothing: the text is not a number at all.
        diags.emit(D::LANG_SYNTAX_UNEXPECTED_CHAR, span(start, pos),
                   { { "char", std::format("'{}'", text.substr(start, pos - start)) } });
        if (pos == start)
          // A digit of another script, which nothing here consumes.
          advance();
      }
      if (is_float_type_name(suffix)) {
        // Whole digits and a floating-point suffix: `3f64` is three as a float.
        auto& t = emit(TokKind::float_lit, start);
        t.float_value = negative ? -double(value) : double(value);
        t.type_suffix = suffix;
        return;
      }
      auto& t = emit(TokKind::int_lit, start);
      t.int_value = negative ? -__int128(value) : __int128(value);
      t.int_too_large = too_large;
      t.type_suffix = suffix;
    }


    bool Lexer::begins_a_fraction(int radix) const noexcept
    {
      // A point with a digit after it, or an exponent: `e` for decimal, `p`
      // for hexadecimal, as in C.  `1.x` is a member and not a number.
      if (peek() == U'.' && digit_follows(1, radix))
        return true;
      char32_t marker = radix == 16 ? U'p' : U'e';
      if (ascii_lower(peek()) != marker)
        return false;
      char32_t sign = peek(1);
      unsigned offset = sign == U'+' || sign == U'-' || sign == end_of_text ? 2 : 1;
      return unicode::is_digit(peek(offset));
    }


    bool Lexer::digit_follows(unsigned offset, int radix) const noexcept
    {
      return in(radix == 16 ? hex_digits : decimal_digits, peek(offset));
    }


    void Lexer::lex_fraction(std::size_t start, std::size_t digits_start, int radix, bool negative)
    {
      // The C forms, decimal and hexadecimal, read by the library's reader of
      // them, which rounds as Python's does.
      std::string_view fraction_digits = radix == 16 ? hex_digits : decimal_digits;
      if (peek() == U'.') {
        ++pos;
        while (in(fraction_digits, peek()))
          ++pos;
      }
      char32_t marker = radix == 16 ? U'p' : U'e';
      if (ascii_lower(peek()) == marker) {
        ++pos;
        if (peek() == U'+' || peek() == U'-')
          ++pos;
        while (unicode::is_digit(peek()) || peek() == U'_')
          advance();
      }
      std::string body;
      for (char c : text.substr(digits_start, pos - digits_start))
        if (c != '_')
          body += c;
      auto suffix = lex_literal_suffix(true, false);
      double value = 0.0;
      auto format = radix == 16 ? std::chars_format::hex : std::chars_format::general;
      auto [end, ec] = std::from_chars(body.data(), body.data() + body.size(), value, format);
      if (ec == std::errc::result_out_of_range)
        // Python makes an infinity of what is too large and nought of what is
        // too small, which is what strtod does.
        value = std::strtod((radix == 16 ? "0x" + body : body).c_str(), nullptr);
      else if (ec != std::errc{} || end != body.data() + body.size()) [[unlikely]] {
        diags.emit(D::LANG_SYNTAX_UNEXPECTED_CHAR, span(start, pos),
                   { { "char", std::format("'{}'", text.substr(start, pos - start)) } });
        value = 0.0;
      }
      auto& t = emit(TokKind::float_lit, start);
      t.float_value = negative ? -value : value;
      t.type_suffix = suffix;
    }


    std::string_view Lexer::lex_literal_suffix(bool floating, bool either)
    {
      if (! is_ident_start(peek()))
        return {};
      auto start = pos;
      while (is_ident_continue(peek()))
        advance();
      auto suffix = text.substr(start, pos - start);
      bool wanted = either ? is_integer_type_name(suffix) || is_float_type_name(suffix)
                           : floating ? is_float_type_name(suffix) : is_integer_type_name(suffix);
      if (! wanted) [[unlikely]] {
        diags.emit(D::LANG_SYNTAX_BAD_LITERAL_SUFFIX, span(start, pos), { { "suffix", suffix } });
        return {};
      }
      return suffix;
    }


    void Lexer::lex_string(std::size_t start)
    {
      ++pos;
      std::string value;
      while (true) {
        char32_t ch = peek();
        if (ch == end_of_text || ch == U'\n') [[unlikely]] {
          diags.emit(D::LANG_SYNTAX_UNTERMINATED_STRING, span(start, pos));
          break;
        }
        if (ch == U'"') {
          ++pos;
          break;
        }
        if (ch == U'\\') {
          value += lex_escape();
          continue;
        }
        auto n = unicode::decode(text, pos).length;
        value.append(text.substr(pos, n));
        pos += n;
      }
      emit(TokKind::string_lit, start).str_value = std::move(value);
    }


    void Lexer::lex_character(std::size_t start)
    {
      // One character between apostrophes, with the escapes a string takes.
      // Each escape counts as one, even one that denotes nothing, as Python
      // counts the pieces it collects.
      ++pos;
      std::vector<std::string> found;
      bool closed = false;
      while (true) {
        char32_t ch = peek();
        if (ch == end_of_text || ch == U'\n')
          break;
        if (ch == U'\'') {
          ++pos;
          closed = true;
          break;
        }
        if (ch == U'\\') {
          found.push_back(lex_escape());
          continue;
        }
        auto n = unicode::decode(text, pos).length;
        found.emplace_back(text.substr(pos, n));
        pos += n;
      }
      if (! closed) [[unlikely]] {
        diags.emit(D::LANG_SYNTAX_UNTERMINATED_CHARACTER, span(start, pos));
        emit(TokKind::char_lit, start);
        return;
      }
      if (found.size() != 1) [[unlikely]] {
        diags.emit(D::LANG_SYNTAX_CHARACTER_IS_ONE, span(start, pos), { { "found", found.size() } });
        emit(TokKind::char_lit, start);
        return;
      }
      emit(TokKind::char_lit, start).int_value = found[0].empty() ? 0 : unicode::decode(found[0], 0).cp;
    }


    std::string Lexer::lex_escape()
    {
      auto start = pos;
      ++pos;
      char32_t ch = peek();
      switch (ch) {
      case U'a': ++pos; return "\a";
      case U'b': ++pos; return "\b";
      case U'f': ++pos; return "\f";
      case U'n': ++pos; return "\n";
      case U'r': ++pos; return "\r";
      case U't': ++pos; return "\t";
      case U'v': ++pos; return "\v";
      case U'\\': ++pos; return "\\";
      case U'\'': ++pos; return "'";
      case U'"': ++pos; return "\"";
      case U'0': ++pos; return std::string(1, '\0');
      default: break;
      }
      std::string shown;
      if (ch != end_of_text)
        unicode::append(shown, ch);
      if (ch == U'u' || ch == U'U') {
        unsigned want = ch == U'u' ? 4 : 8;
        ++pos;
        unsigned got = 0;
        char32_t value = 0;
        for (; got < want && in("0123456789abcdefABCDEF", peek()); ++got, ++pos) {
          char c = text[pos];
          value = value * 16 + char32_t(c <= '9' ? c - '0' : (c | 0x20) - 'a' + 10);
        }
        if (got == want && value <= 0x10ffff) {
          std::string out;
          unicode::append(out, value);
          return out;
        }
        diags.emit(D::LANG_SYNTAX_BAD_ESCAPE, span(start, pos), { { "char", shown } });
        return {};
      }
      if (ch != end_of_text)
        advance();
      diags.emit(D::LANG_SYNTAX_BAD_ESCAPE, span(start, pos), { { "char", shown } });
      return {};
    }

  } // anonymous namespace


  bool is_operator_opener(char32_t cp) noexcept
  {
    return ! not_nameable(cp) && ! contains(operator_glyphs, cp) && unicode::is_open_punctuation(cp);
  }


  bool is_operator_closer(char32_t cp) noexcept
  {
    return ! not_nameable(cp) && ! contains(operator_glyphs, cp) && unicode::is_close_punctuation(cp);
  }


  bool is_operator_glyph(char32_t cp) noexcept
  {
    // Unicode decides most of it; what its categories leave out is the ASCII
    // the grammar needs for itself.  A glyph the language already uses as an
    // operator qualifies whatever Unicode calls it.
    if (not_nameable(cp))
      return false;
    return unicode::is_symbol(cp) || contains(operator_glyphs, cp);
  }


  bool is_operator_name(std::string_view written) noexcept
  {
    auto count = unicode::length(written);
    auto first = unicode::decode(written, 0);
    if (count == 1)
      return is_operator_glyph(first.cp);
    return count == 2 && is_operator_opener(first.cp) && is_operator_closer(unicode::decode(written, first.length).cp);
  }


  bool is_a_name(std::string_view text) noexcept
  {
    if (text.empty())
      return false;
    auto first = unicode::decode(text, 0);
    if (! is_ident_start(first.cp) || first.cp == glyph::builtin)
      return false;
    for (std::size_t at = first.length; at < text.size();) {
      auto d = unicode::decode(text, at);
      if (! is_ident_continue(d.cp))
        return false;
      at += d.length;
    }
    return keyword_kind(text) == TokKind::ident;
  }


  std::vector<Token> tokenize(const SourceFile& source, DiagEngine& diags, bool lazy)
  {
    return Lexer(source, diags, lazy, 0, source.text.size()).run();
  }


  std::vector<Token> tokenize_body(const SourceFile& source, Span span, DiagEngine& diags)
  {
    return Lexer(source, diags, false, std::size_t(span.start - source.base), std::size_t(span.end - source.base)).run();
  }

} // namespace pl4g
