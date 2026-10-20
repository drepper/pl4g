// What went into the binary.  A port of pypl4g's sbom.py.
#include "driver/sbom.hh"

#include <unordered_map>

#include "front/ast.hh"
#include "front/repr.hh"
#include "util/pyfloat.hh"
#include "util/sha256.hh"


namespace pl4g::driver::sbom {

  // One token as the hash reads it: its kind and what it holds.
  std::string python_kind_name_and(const Token& token);

  namespace {

    // The marks that say where a block begins and ends, whichever notation
    // was used, and what separates two statements.
    constexpr std::string_view begins = "\x01";
    constexpr std::string_view ends = "\x02";
    constexpr std::string_view separates = "\x03";

    std::string decimal(__int128 v)
    {
      if (v == 0)
        return "0";
      bool negative = v < 0;
      unsigned __int128 m = negative ? -(unsigned __int128)(v) : (unsigned __int128)(v);
      std::string digits;
      for (; m > 0; m /= 10)
        digits.insert(digits.begin(), char('0' + int(m % 10)));
      return negative ? "-" + digits : digits;
    }

    // Whether the colon at `at` is the one that introduces a layout block:
    // a newline and an indent follow it.
    bool opens_a_block(std::span<const Token> tokens, std::size_t at)
    {
      return at + 2 < tokens.size() && tokens[at + 1].kind == TokKind::newline
             && tokens[at + 2].kind == TokKind::indent;
    }

    // What a token holds, written so that two different tokens differ: a
    // literal as the value it stands for.
    std::string text_of(const Token& token)
    {
      switch (token.kind) {
      case TokKind::int_lit:
      case TokKind::char_lit:
      case TokKind::exponent:
        return decimal(token.int_value) + " " + std::string(token.type_suffix);
      case TokKind::float_lit:
        return python_repr(token.float_value) + " " + std::string(token.type_suffix);
      case TokKind::string_lit:
        return token.str_value;
      default:
        return std::string(token.text);
      }
    }

    // Where an item begins and ends, what it says about itself included.
    Span extent_of(const ast::Node* item)
    {
      Span span = item->span;
      auto take = [&](std::span<const ast::Attribute* const> attrs) {
        for (auto one : attrs)
          span = span.to(one->span);
      };
      if (auto f = ast::as<ast::FuncDef>(item))
        take(f->attrs);
      else if (auto t = ast::as<ast::TypeDef>(item))
        take(t->attrs);
      else if (auto e = ast::as<ast::EnumDef>(item))
        take(e->attrs);
      else if (auto v = ast::as<ast::VarDef>(item))
        take(v->attrs);
      else if (auto u = ast::as<ast::UnitDef>(item))
        take(u->attrs);
      return span;
    }

    // One row per definition of one source file -- of a module, one per
    // function it was used for, which is every one whose body was read.
    void definitions_of(std::vector<Entry>& made, const ast::SourceUnit* unit, bool module)
    {
      for (auto item : unit->items) {
        Tag tag;
        std::string_view name;
        const ast::Block* body = nullptr;
        switch (item->tag) {
        case ast::NodeKind::FuncDef: {
          auto f = static_cast<const ast::FuncDef*>(item);
          tag = Tag::function;
          name = f->name;
          body = f->body;
          break;
        }
        case ast::NodeKind::TypeDef:
          tag = Tag::type;
          name = static_cast<const ast::TypeDef*>(item)->name;
          break;
        case ast::NodeKind::EnumDef:
          tag = Tag::type;
          name = static_cast<const ast::EnumDef*>(item)->name;
          break;
        case ast::NodeKind::VarDef:
          tag = Tag::variable;
          name = static_cast<const ast::VarDef*>(item)->name;
          break;
        case ast::NodeKind::UnitDef:
          tag = Tag::unit;
          name = static_cast<const ast::UnitDef*>(item)->name;
          break;
        default:
          continue;
        }
        bool deferred = body != nullptr && body->job != nullptr;
        if (module && (tag != Tag::function || ! deferred || ! body->parsed()))
          continue;
        Span span = extent_of(item);
        std::vector<Token> held;
        for (const auto& token : unit->tokens)
          if (token.span.start >= span.start && token.span.end <= span.end && token.kind != TokKind::body)
            held.push_back(token);
        if (deferred)
          for (const auto& token : body->job->lexed())
            held.push_back(token);
        made.push_back({ tag, name.empty() ? std::string("\N{RIGHTWARDS ARROW}") : std::string(name),
                         digest_of(held) });
      }
    }

  } // anonymous namespace


  std::vector<std::string> normalized(std::span<const Token> tokens)
  {
    std::vector<std::string> made;
    for (std::size_t at = 0; at < tokens.size(); ++at) {
      const Token& token = tokens[at];
      switch (token.kind) {
      case TokKind::eof:
        break;
      case TokKind::colon:
        // The indent that follows a layout block's colon says it again.
        if (! opens_a_block(tokens, at))
          made.push_back(python_kind_name_and(token));
        break;
      case TokKind::indent:
      case TokKind::lbrace:
        made.emplace_back(begins);
        break;
      case TokKind::dedent:
      case TokKind::rbrace:
        // A block written by indentation ends after the newline that ended
        // its last statement; one written with braces does not.
        if (! made.empty() && made.back() == separates && token.kind == TokKind::dedent)
          made.pop_back();
        made.emplace_back(ends);
        break;
      case TokKind::newline:
        // The end of the line a block's colon was written on separates nothing.
        if (at + 1 < tokens.size() && tokens[at + 1].kind == TokKind::indent)
          break;
        made.emplace_back(separates);
        break;
      case TokKind::semicolon:
        made.emplace_back(separates);
        break;
      default:
        made.push_back(python_kind_name_and(token));
        break;
      }
    }
    while (! made.empty() && made.back() == separates)
      made.pop_back();
    std::size_t at = 0;
    // Nothing begins by ending a block.
    while (at < made.size() && (made[at] == ends || made[at] == separates))
      ++at;
    return { made.begin() + std::ptrdiff_t(at), made.end() };
  }


  std::string python_kind_name_and(const Token& token)
  {
    return ast::python_kind_name(token.kind) + " " + text_of(token);
  }


  std::string digest_of(std::span<const Token> tokens)
  {
    Sha256 made;
    for (const auto& piece : normalized(tokens)) {
      made.update(piece);
      made.update(std::string_view("\0", 1));
    }
    return made.hexdigest();
  }


  std::string digest_of_text(std::string_view text)
  {
    Sha256 made;
    made.update(text);
    return made.hexdigest();
  }


  std::string digest_of_digests(std::span<const std::string> digests)
  {
    Sha256 made;
    for (const auto& one : digests) {
      made.update(one);
      made.update(std::string_view("\0", 1));
    }
    return made.hexdigest();
  }


  std::vector<Entry> entries_for(const SourceManager& sources, std::string_view compiler)
  {
    std::vector<Entry> made{ { Tag::compiler, std::string(compiler), digest_of_text(compiler) } };
    std::vector<std::string> each;
    // A source is hashed by its bytes, which every file has whole.
    for (const auto& one : sources.read_units) {
      std::string_view text;
      for (const auto& file : sources.files())
        if (file.path == one.path) {
          text = file.text;
          break;
        }
      auto digest = digest_of_text(text);
      each.push_back(digest);
      made.push_back({ Tag::source, one.path.generic_string(), digest });
    }
    made.push_back({ Tag::sources, "sources", digest_of_digests(each) });
    for (const auto& one : sources.read_units)
      if (one.unit != nullptr)
        definitions_of(made, static_cast<const ast::SourceUnit*>(one.unit), one.module);
    return made;
  }


  Table build(std::span<const Entry> entries, bool little_endian)
  {
    Table table;
    // A string table begins with a nought byte, so that offset nought is the
    // empty string.
    table.strings.push_back(0);
    std::unordered_map<std::string, std::uint32_t> where{ { "", 0 } };
    auto put = [&](const std::string& text) {
      auto found = where.find(text);
      if (found != where.end())
        return found->second;
      auto at = std::uint32_t(table.strings.size());
      table.strings.insert(table.strings.end(), text.begin(), text.end());
      table.strings.push_back(0);
      where.emplace(text, at);
      return at;
    };
    auto word = [&](std::uint32_t value) {
      for (int i = 0; i < 4; ++i)
        table.rows.push_back(static_cast<unsigned char>(little_endian ? value >> (8 * i) : value >> (8 * (3 - i))));
    };
    for (const auto& entry : entries) {
      word(put(entry.digest));
      word(std::uint32_t(entry.tag));
      word(put(entry.name));
    }
    table.entries.assign(entries.begin(), entries.end());
    return table;
  }

} // namespace pl4g::driver::sbom
