// The manual of PL4G, served from the text embedded in the library.
#include "manual/manual.hh"

#include <algorithm>
#include <cctype>
#include <ranges>
#include <format>
#include <string>
#include <utility>
#include <vector>

#include <fstrcmp.h>


namespace pl4g::manual {

  namespace {

    // What bin/pl4g-manual packed: each name, a nul, its text, a nul, in the
    // order of the names.
    constexpr unsigned char packed[] = {
#embed "manual.pack"
    };

    // How close a name has to be to be offered in place of one that is not
    // there, as fmemcmp measures it, and how many are offered at most.
    constexpr double close_enough = FSTRCMP_THRESHOLD;
    constexpr std::size_t offered = 3;

    // The nodes, their names and their texts: views of what is embedded.
    struct Nodes {
      std::vector<std::string_view> names;
      std::vector<std::string_view> texts;
    };

    // The nodes, read out of what was packed the first time they are asked
    // for.
    const Nodes& nodes()
    {
      static const Nodes read = [] {
        Nodes made;
        std::string_view rest(reinterpret_cast<const char*>(packed), sizeof packed);
        while (! rest.empty()) {
          auto name_end = rest.find('\0');
          auto text_end = rest.find('\0', name_end + 1);
          made.names.push_back(rest.substr(0, name_end));
          made.texts.push_back(rest.substr(name_end + 1, text_end - name_end - 1));
          rest.remove_prefix(text_end + 1);
        }
        return made;
      }();
      return read;
    }

    // Where `name` is among the names, or the end.
    std::size_t index_of(std::string_view name) noexcept
    {
      const auto& all = nodes().names;
      auto found = std::ranges::lower_bound(all, name);
      return found != all.end() && *found == name ? std::size_t(found - all.begin()) : all.size();
    }

    // `text` with ASCII letters in lower case, which is how names are
    // compared for closeness: `JSON.Decode` is meant as `json.decode`.
    std::string lowered(std::string_view text)
    {
      std::string out(text);
      for (auto& c : out)
        c = char(std::tolower(static_cast<unsigned char>(c)));
      return out;
    }

    // The names closest to `name`, the closest first, where any is close
    // enough to be what was meant.
    std::vector<std::string_view> close_to(std::string_view name)
    {
      auto wanted = lowered(name);
      std::vector<std::pair<double, std::string_view>> scored;
      for (auto one : nodes().names) {
        auto candidate = lowered(one);
        double score = ::fmemcmp(wanted.data(), wanted.size(), candidate.data(), candidate.size());
        if (score >= close_enough)
          scored.emplace_back(score, one);
      }
      std::ranges::stable_sort(scored, std::ranges::greater{}, &std::pair<double, std::string_view>::first);
      std::vector<std::string_view> out;
      for (const auto& [_, one] : scored | std::views::take(offered))
        out.push_back(one);
      return out;
    }

  } // anonymous namespace


  std::string_view lookup(std::string_view name)
  {
    if (auto at = index_of(name); at < nodes().names.size())
      return nodes().texts[at];
    // The answer to a name that is not there is made each time, so it has to
    // be kept somewhere: the thread's own, until it asks again.
    thread_local std::string answer;
    answer = std::format("There is no node named '{}'.", name);
    auto close = close_to(name);
    if (close.size() == 1)
      answer += std::format("  Did you mean '{}'?", close.front());
    else if (! close.empty()) {
      answer += "  Did you mean ";
      for (std::size_t at = 0; at < close.size(); ++at)
        answer += std::format("{}'{}'", at == 0 ? "" : at + 1 == close.size() ? " or " : ", ", close[at]);
      answer += "?";
    }
    answer += "  Ask for 'help' to start from the top.\n";
    return answer;
  }


  bool exists(std::string_view name) noexcept
  {
    return index_of(name) < nodes().names.size();
  }


  std::span<const std::string_view> names() noexcept
  {
    return nodes().names;
  }

} // namespace pl4g::manual
