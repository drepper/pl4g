// What a RISC-V program is built for.
#include "target/riscv64/isa.hh"

#include <algorithm>
#include <format>
#include <set>
#include <vector>


namespace pl4g::target::riscv64::isa {

  namespace {

    using Names = std::vector<std::string_view>;

    // What one extension brings with it, closed over in `closed`.
    const std::vector<std::pair<std::string_view, Names>>& implies()
    {
      static const std::vector<std::pair<std::string_view, Names>> made{
        { "g", { "i", "m", "a", "f", "d", "zicsr", "zifencei" } },
        { "b", { "zba", "zbb", "zbs" } },
        { "d", { "f" } },
        { "q", { "d" } },
        { "f", { "zicsr" } },
        { "zfa", { "f" } },
        { "zfh", { "f" } },
        { "zfhmin", { "f" } },
        { "zicntr", { "zicsr" } },
        { "zihpm", { "zicsr" } },
        { "zk", { "zkn", "zkr", "zkt" } },
        { "zkn", { "zbkb", "zbkc", "zbkx", "zkne", "zknd", "zknh" } },
        { "zks", { "zbkb", "zbkc", "zbkx", "zksed", "zksh" } },
        { "v", { "zve64d", "zvl128b" } },
        { "zve64d", { "zve64f" } },
        { "zve64f", { "zve64x", "zve32f" } },
        { "zve64x", { "zve32x", "zvl64b" } },
        { "zve32f", { "zve32x" } },
        { "zve32x", { "zvl32b" } },
        { "zvl128b", { "zvl64b" } },
        { "zvl64b", { "zvl32b" } },
        { "zvbb", { "zvkb" } },
        { "zvfhmin", { "zve32f" } },
        { "zvfh", { "zve32f", "zvfhmin", "zfhmin" } },
        { "sha", { "h", "ssstateen", "shcounterenw", "shvstvala", "shtvala", "shvstvecd", "shvsatpa", "shgatpa" } },
        { "zvkng", { "zvkb" } },
        { "zvksg", { "zvkb" } },
        { "m", { "zmmul" } },
        { "a", { "zaamo", "zalrsc" } },
        { "c", { "zca" } },
        { "zcb", { "zca" } },
        { "zcd", { "zca" } },
        { "zcf", { "zca" } },
        { "zcmop", { "zca" } },
      };
      return made;
    }

    const Names* implied_by(std::string_view name)
    {
      for (const auto& [one, implied] : implies())
        if (one == name)
          return &implied;
      return nullptr;
    }

    // What an extension brings only where another is there too, and only at
    // the stated width where one is stated.
    struct ImpliedWith {
      std::string_view one;
      std::string_view other;
      std::string_view implied;
      std::optional<int> only_at;
    };
    constexpr ImpliedWith implies_with[] = {
      { "c", "d", "zcd", std::nullopt },
      { "c", "f", "zcf", 32 },
    };

    // What version each extension is at, where it is not the first.
    const std::vector<std::pair<std::string_view, std::pair<int, int>>>& versions_known()
    {
      static const std::vector<std::pair<std::string_view, std::pair<int, int>>> made{
        { "i", { 2, 1 } }, { "e", { 2, 0 } }, { "m", { 2, 0 } }, { "a", { 2, 1 } }, { "f", { 2, 2 } },
        { "d", { 2, 2 } }, { "q", { 2, 2 } }, { "c", { 2, 0 } },
        { "zicsr", { 2, 0 } }, { "zifencei", { 2, 0 } }, { "zicntr", { 2, 0 } }, { "zihpm", { 2, 0 } },
        { "zihintpause", { 2, 0 } },
      };
      return made;
    }

    // Every extension name this compiler knows.
    const std::set<std::string, std::less<>>& known()
    {
      static const std::set<std::string, std::less<>> made = [] {
        std::set<std::string, std::less<>> all;
        for (char c : single)
          all.insert(std::string(1, c));
        for (std::string_view one : {
               "e", "g",
               "zicsr", "zifencei", "zicntr", "zihpm", "ziccif", "ziccrse", "ziccamoa",
               "ziccamoc", "zicclsm", "zic64b", "zicbom", "zicbop", "zicboz", "zicond",
               "zihintpause", "zihintntl", "zimop", "zcmop", "zcb", "zcd", "zcf",
               "za64rs", "zawrs", "zabha", "zacas", "zama16b",
               "zfa", "zfh", "zfhmin", "zfbfmin", "zfinx", "zdinx",
               "zba", "zbb", "zbs", "zbc", "zbkb", "zbkc", "zbkx",
               "zk", "zkn", "zknd", "zkne", "zknh", "zkr", "zks", "zksed", "zksh", "zkt",
               "zve32x", "zve32f", "zve64x", "zve64f", "zve64d",
               "zvl32b", "zvl64b", "zvl128b", "zvl256b", "zvl512b", "zvl1024b", "zvkb",
               "zmmul", "zaamo", "zalrsc", "zca",
               "zvbb", "zvbc", "zvfh", "zvfhmin", "zvfbfmin", "zvfbfwma", "zvkt",
               "zvkng", "zvksg",
               "zicfilp", "zicfiss",
               "ss", "sm", "sha", "supm", "sspm", "ssnpm", "svbare", "sv32", "sv39",
               "sv48", "sv57", "svade", "svadu", "svinval", "svnapot", "svpbmt", "svvptc",
               "ssccptr", "sstvecd", "sstvala", "sscounterenw", "sstc", "sscofpmf",
               "ssu64xl", "ssstateen", "ssstrict", "sdtrig", "shcounterenw", "shvstvala",
               "shtvala", "shvstvecd", "shvsatpa", "shgatpa" })
          all.insert(std::string(one));
        return all;
      }();
      return made;
    }

    // The profiles this compiler knows, and their mandatory extensions.
    struct Profile {
      std::string_view name;
      int bits;
      Names extensions;
    };

    const std::vector<Profile>& profiles()
    {
      static const std::vector<Profile> made{
        { "rva23u64", 64, {
            "i", "m", "a", "f", "d", "c", "b", "v",
            "zicsr", "zicntr", "zihpm", "ziccif", "ziccrse", "ziccamoa",
            "zicclsm", "za64rs", "zihintpause", "zic64b", "zicbom", "zicbop",
            "zicboz", "zfhmin", "zkt", "zvfhmin", "zvbb", "zvkt", "zihintntl",
            "zicond", "zimop", "zcmop", "zcb", "zfa", "zawrs", "supm" } },
        { "rva23s64", 64, {
            "i", "m", "a", "f", "d", "c", "b", "v",
            "zicsr", "zicntr", "zihpm", "ziccif", "ziccrse", "ziccamoa",
            "zicclsm", "za64rs", "zihintpause", "zic64b", "zicbom", "zicbop",
            "zicboz", "zfhmin", "zkt", "zvfhmin", "zvbb", "zvkt", "zihintntl",
            "zicond", "zimop", "zcmop", "zcb", "zfa", "zawrs", "supm",
            "zifencei", "svbare", "svade", "ssccptr", "sstvecd",
            "sstvala", "sscounterenw", "svpbmt", "svinval", "svnapot", "sstc",
            "sscofpmf", "ssnpm", "ssu64xl", "sha" } },
      };
      return made;
    }

    // What a profile name without its privilege level or width means.
    std::string_view short_for(std::string_view name)
    {
      if (name == "rva23" || name == "rva23u")
        return "rva23u64";
      if (name == "rva23s")
        return "rva23s64";
      return name;
    }

    bool is_digit(char c) noexcept { return c >= '0' && c <= '9'; }

    // Whether `text` has the shape of a profile name: a class, a year, and
    // optionally a privilege level and a width.
    bool profile_shaped(std::string_view text)
    {
      if (text.size() < 5 || ! text.starts_with("rv") || ! std::string_view("abim").contains(text[2])
          || ! is_digit(text[3]) || ! is_digit(text[4]))
        return false;
      auto rest = text.substr(5);
      if (rest.empty())
        return true;
      if (! std::string_view("usm").contains(rest[0]))
        return false;
      rest.remove_prefix(1);
      return rest.empty() || rest == "32" || rest == "64";
    }

    // Whether all of `text` is a version: digits, optionally `p` and digits.
    bool version_shaped(std::string_view text)
    {
      std::size_t at = 0;
      while (at < text.size() && is_digit(text[at]))
        ++at;
      if (at == 0)
        return false;
      if (at == text.size())
        return true;
      if (text[at] != 'p')
        return false;
      auto minor = text.substr(at + 1);
      return ! minor.empty() && std::ranges::all_of(minor, is_digit);
    }

    std::string spelled(std::pair<int, int> version)
    {
      return std::format("{}p{}", version.first, version.second);
    }

    Version version(std::string_view text)
    {
      if (text.empty())
        return std::nullopt;
      if (! version_shaped(text)) [[unlikely]]
        throw BadName(std::format("'{}' is not a version; a version is a number, or two with a 'p' between them",
                                  text));
      auto p = text.find('p');
      auto number = [](std::string_view digits) {
        auto first = digits.find_first_not_of('0');
        return first == std::string_view::npos ? std::string("0") : std::string(digits.substr(first));
      };
      return std::pair{ number(text.substr(0, p)), p == std::string_view::npos ? "0" : number(text.substr(p + 1)) };
    }

    // `found` with everything its extensions imply, and with `g` spelled out.
    void closed(OrderedMap<std::string, Version>& found, int bits)
    {
      std::vector<std::string> waiting = found.keys();
      while (! waiting.empty()) {
        auto name = std::move(waiting.back());
        waiting.pop_back();
        if (auto implied = implied_by(name))
          for (auto one : *implied)
            if (! found.contains(std::string(one))) {
              found[std::string(one)] = std::nullopt;
              waiting.emplace_back(one);
            }
        for (const auto& with : implies_with) {
          if (with.only_at && *with.only_at != bits)
            continue;
          if (found.contains(std::string(with.one)) && found.contains(std::string(with.other))
              && ! found.contains(std::string(with.implied))) {
            found[std::string(with.implied)] = std::nullopt;
            waiting.emplace_back(with.implied);
          }
        }
      }
      found.erase("g");
    }

    ISA profile(std::string_view lowered, std::string_view written)
    {
      auto wanted = short_for(lowered);
      for (const auto& one : profiles())
        if (one.name == wanted) {
          ISA made{ one.bits, false, std::string(written), {} };
          for (auto name : one.extensions)
            made.versions[std::string(name)] = std::nullopt;
          closed(made.versions, one.bits);
          return made;
        }
      std::vector<std::string_view> names;
      for (const auto& one : profiles())
        names.push_back(one.name);
      std::ranges::sort(names);
      std::string list;
      for (auto name : names)
        list += (list.empty() ? "" : ", ") + std::string(name);
      throw BadName("it has the shape of a profile name and is not one this compiler knows; it knows " + list);
    }

    struct Read {
      std::string name;
      Version version;
      std::size_t end = 0;
    };

    Read read_single(std::string_view text, std::size_t at)
    {
      std::string name(1, text[at]);
      if (! known().contains(name)) [[unlikely]]
        throw BadName(std::format("'{}' is not one of the single-letter extensions, which are {}, e and g", name,
                                  single));
      auto end = at + 1;
      while (end < text.size() && (is_digit(text[end]) || text[end] == 'p'))
        ++end;
      return { name, version(text.substr(at + 1, end - at - 1)), end };
    }

    Read read_multi(std::string_view text, std::size_t at)
    {
      auto end = at;
      while (end < text.size() && text[end] != '_')
        ++end;
      auto whole = text.substr(at, end - at);
      if (known().contains(whole))
        return { std::string(whole), std::nullopt, end };
      // The leftmost place from which the rest is a version.
      for (std::size_t start = 1; start < whole.size(); ++start)
        if (version_shaped(whole.substr(start))) {
          if (known().contains(whole.substr(0, start)))
            return { std::string(whole.substr(0, start)), version(whole.substr(start)), end };
          break;
        }
      throw BadName(std::format("'{}' is not an extension this compiler knows", whole));
    }

    ISA string(std::string_view lowered, std::string_view written)
    {
      if (lowered.size() < 5 || (lowered.substr(0, 4) != "rv32" && lowered.substr(0, 4) != "rv64")) [[unlikely]]
        throw BadName("an ISA string begins with 'rv32' or 'rv64' and a profile name with a class and a year, "
                      "like 'rva23'");
      OrderedMap<std::string, Version> found;
      std::size_t at = 4;
      while (at < lowered.size()) {
        if (lowered[at] == '_') {
          ++at;
          continue;
        }
        auto one = std::string_view("zsx").contains(lowered[at]) ? read_multi(lowered, at) : read_single(lowered, at);
        if (found.contains(one.name)) [[unlikely]]
          throw BadName(std::format("'{}' is written twice", one.name));
        found[one.name] = one.version;
        at = one.end;
      }
      int bits = lowered[2] == '3' ? 32 : 64;
      closed(found, bits);
      if (! found.contains("i") && ! found.contains("e")) [[unlikely]]
        throw BadName("an ISA string names its base, which is 'i' or 'e' -- or 'g', which brings 'i' with it");
      bool embedded = found.contains("e");
      return ISA{ bits, embedded, std::string(written), std::move(found) };
    }

  } // anonymous namespace


  std::pair<int, int> version_of(std::string_view extension)
  {
    for (const auto& [name, version] : versions_known())
      if (name == extension)
        return version;
    return { 1, 0 };
  }


  std::string ISA::normalized() const
  {
    std::string base = std::format("rv{}{}{}", bits, embedded ? "e" : "i", spelled(version_of(embedded ? "e" : "i")));
    std::vector<std::string> rest;
    for (char letter : single)
      if (letter != 'i' && versions.contains(std::string(1, letter)))
        rest.emplace_back(1, letter);
    // The multi-letter ones: the standard unprivileged first, by the letter
    // after the `z` and then alphabetically, then the privileged, then the rest.
    for (char prefix : std::string_view("zsx")) {
      std::vector<std::string> here;
      for (const auto& [name, _] : versions)
        if (name.size() > 1 && name[0] == prefix)
          here.push_back(name);
      if (prefix == 'z')
        std::ranges::stable_sort(here, [](const std::string& a, const std::string& b) {
          auto rank = [](const std::string& n) {
            auto at = single.find(n[1]);
            return at == std::string_view::npos ? -1 : int(at);
          };
          return std::pair(rank(a), a) < std::pair(rank(b), b);
        });
      else
        std::ranges::sort(here);
      rest.insert(rest.end(), here.begin(), here.end());
    }
    std::string out = base;
    for (const auto& name : rest)
      out += "_" + name + spelled(version_of(name));
    return out;
  }


  ISA parse(std::string_view text)
  {
    auto first = text.find_first_not_of(" \t\n\r\f\v");
    auto last = text.find_last_not_of(" \t\n\r\f\v");
    std::string written = first == std::string_view::npos ? std::string() : std::string(text.substr(first, last - first + 1));
    std::string lowered = written;
    for (auto& c : lowered)
      if (c >= 'A' && c <= 'Z')
        c = char(c - 'A' + 'a');
    if (lowered.empty()) [[unlikely]]
      throw BadName("a name was expected");
    if (profile_shaped(lowered))
      return profile(lowered, written);
    return string(lowered, written);
  }

} // namespace pl4g::target::riscv64::isa
