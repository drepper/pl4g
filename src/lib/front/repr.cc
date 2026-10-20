// The syntax tree and the tokens as pypl4g writes them.
#include "front/repr.hh"

#include <algorithm>
#include <format>
#include <unordered_map>
#include <vector>

#include "util/pyfloat.hh"
#include "util/pyrepr.hh"
#include "util/unicode.hh"


namespace pl4g::ast {

  namespace {

    consteval auto unchecked() { return std::meta::access_context::unchecked(); }

    // A name as pypl4g spells it: without the underscore a C++ keyword made us
    // add.
    constexpr std::string_view python_name(std::string_view name)
    {
      return name.ends_with('_') ? name.substr(0, name.size() - 1) : name;
    }

    std::string upper(std::string_view name)
    {
      std::string out(python_name(name));
      for (auto& c : out)
        if (c >= 'a' && c <= 'z')
          c = char(c - 'a' + 'A');
      return out;
    }

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


    // Where a location is in pypl4g's coordinates, which count characters
    // where these count bytes: each file's base the one after the end of the
    // file before it, and every offset in it a number of characters.
    class Characters {
    public:
      explicit Characters(const SourceManager& sources) : sources(sources)
      {
        Loc base = 0;
        for (const auto& file : sources.files()) {
          bases.push_back(base);
          base += Loc(unicode::length(file.text)) + 1;
        }
      }

      Loc operator()(Loc loc)
      {
        if (loc < 0)
          return loc;
        auto file = sources.file_of(loc);
        if (file == nullptr)
          return loc;
        auto& counted = within[file->ident];
        if (counted.empty()) {
          counted.resize(file->text.size() + 2, 0);
          std::uint32_t chars = 0;
          for (std::size_t at = 0; at <= file->text.size(); ++at) {
            counted[at] = chars;
            if (at < file->text.size() && (static_cast<unsigned char>(file->text[at]) & 0xc0) != 0x80)
              ++chars;
          }
          counted[file->text.size() + 1] = chars + 1;
        }
        auto offset = std::size_t(loc - file->base);
        // A character counts once it begins: an offset inside one is where it
        // begins, which no span of pypl4g's is.
        auto chars = offset < counted.size() ? counted[offset] : counted.back();
        return bases[file->ident] + Loc(chars);
      }

    private:
      const SourceManager& sources;
      std::vector<Loc> bases;
      std::unordered_map<FileId, std::vector<std::uint32_t>> within;
    };


    class Repr {
    public:
      explicit Repr(const SourceManager& sources) : at(sources) {}

      void node(std::string& out, const Node* n)
      {
        if (n == nullptr) {
          out += "None";
          return;
        }
        visit(*n, [&]<typename T>(const T& v) {
          if constexpr (std::is_same_v<T, Block>) {
            // A body the parser set aside is pypl4g's DeferredBlock, which
            // says what a block says.
            out += v.job != nullptr || v.set_aside ? "DeferredBlock(" : "Block(";
            out += "span=";
            span(out, v.span);
            out += ", style=";
            value(out, v.style);
            out += ", stmts=";
            value(out, v.stmts());
            out += ')';
          } else {
            out += std::meta::identifier_of(^^T);
            out += '(';
            bool first = true;
            fields(out, v, first);
            out += ')';
          }
        });
      }

    private:
      void span(std::string& out, Span s)
      {
        out += std::format("Span(start={}, end={})", at(s.start), at(s.end));
      }

      template<typename T>
      void fields(std::string& out, const T& v, bool& first)
      {
        template for (constexpr auto b : std::define_static_array(std::meta::bases_of(^^T, unchecked())))
          fields(out, static_cast<const [:std::meta::type_of(b):]&>(v), first);
        template for (constexpr auto m :
                      std::define_static_array(std::meta::nonstatic_data_members_of(^^T, unchecked()))) {
          if constexpr (std::meta::annotations_of_with_type(m, ^^Hidden).size() == 0
                        && std::meta::identifier_of(m) != "tag") {
            if (! first)
              out += ", ";
            first = false;
            out += python_name(std::meta::identifier_of(m));
            out += '=';
            value(out, v.[:m:]);
          }
        }
      }

      // The members of a struct that is no node, as the tuple pypl4g holds.
      template<typename T>
      void tuple(std::string& out, const T& v)
      {
        out += '(';
        bool first = true;
        template for (constexpr auto m :
                      std::define_static_array(std::meta::nonstatic_data_members_of(^^T, unchecked()))) {
          if (! first)
            out += ", ";
          first = false;
          value(out, v.[:m:]);
        }
        out += ')';
      }

      template<typename T>
      void value(std::string& out, const T& v)
      {
        if constexpr (std::is_pointer_v<T>) {
          if constexpr (std::is_base_of_v<Node, std::remove_cv_t<std::remove_pointer_t<T>>>)
            node(out, v);
          else
            out += "None";
        } else if constexpr (std::is_same_v<T, std::string_view>)
          out += python_str_repr(v);
        else if constexpr (std::is_same_v<T, bool>)
          out += v ? "True" : "False";
        else if constexpr (std::is_same_v<T, Integer>)
          out += decimal(v.value);
        else if constexpr (std::is_same_v<T, double>)
          out += pl4g::python_repr(v);
        else if constexpr (std::is_same_v<T, Span>)
          span(out, v);
        else if constexpr (std::is_enum_v<T>) {
          std::string_view said = enum_name(v);
          // The value pypl4g gives a member is what this describes it as, or
          // its name.
          std::string shown(python_name(describe(v)));
          out += std::format("<{}.{}: {}>", std::meta::identifier_of(^^T), upper(said), python_str_repr(shown));
        } else if constexpr (std::is_integral_v<T>)
          out += decimal(v);
        else if constexpr (requires { v.has_value(); *v; }) {
          if (v.has_value())
            value(out, *v);
          else
            out += "None";
        } else if constexpr (requires { v.begin(); v.end(); v.size(); }) {
          out += '(';
          bool first = true;
          for (const auto& one : v) {
            if (! first)
              out += ", ";
            first = false;
            value(out, one);
          }
          if (v.size() == 1)
            out += ',';
          out += ')';
        } else
          tuple(out, v);
      }

      Characters at;
    };

  } // anonymous namespace


  std::string python_repr(const SourceUnit* unit, const SourceManager& sources)
  {
    std::string out;
    Repr(sources).node(out, unit);
    return out;
  }


  std::string python_kind_name(TokKind kind)
  {
    // pypl4g's names for the kinds, where they are not these uppercased.
    static const std::unordered_map<std::string_view, std::string_view> renamed{
      { "int_lit", "INT" }, { "float_lit", "FLOAT" }, { "string_lit", "STRING" }, { "char_lit", "CHAR" },
      { "op", "OPERATOR" }, { "open_op", "OPEN_OPERATOR" }, { "close_op", "CLOSE_OPERATOR" },
    };
    std::string_view name = enum_name(kind);
    auto found = renamed.find(name);
    return found != renamed.end() ? std::string(found->second) : upper(name);
  }


  std::string token_lines(std::span<const Token> tokens, const SourceManager& sources)
  {
    std::string out;
    for (const auto& token : tokens) {
      auto position = sources.position(token.span.start);
      std::string where = position ? std::format("{}:{}:{}", position->path, position->line, position->column)
                                   : std::string();
      auto pad = [](std::string text, std::size_t width) {
        auto length = unicode::length(text);
        if (length < width)
          text.append(width - length, ' ');
        return text;
      };
      out += pad(where, 28);
      out += pad(python_kind_name(token.kind), 14);
      out += python_str_repr(token.text);
      out += '\n';
    }
    return out;
  }

} // namespace pl4g::ast
