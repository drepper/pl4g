// Check the manual as the library serves it: every node is reached from
// `help` by the names the nodes mention, every name mentioned is a node, none
// is more than four kilobytes, and a name that is not there is answered with
// the close one.  The test the build runs after embedding what bin/pl4g-manual
// made, which checks the same of what it made before it was embedded.
#include <cstddef>
#include <deque>
#include <print>
#include <set>
#include <string>
#include <string_view>

#include "manual/manual.hh"


namespace {

  // The most a node may hold.
  constexpr std::size_t limit = 4096;

  // The names `text` mentions, `[[name]]` each.
  std::set<std::string> mentioned(std::string_view text)
  {
    std::set<std::string> out;
    for (auto at = text.find("[["); at != std::string_view::npos; at = text.find("[[", at + 2)) {
      auto end = text.find("]]", at + 2);
      if (end == std::string_view::npos) [[unlikely]]
        break;
      out.emplace(text.substr(at + 2, end - at - 2));
    }
    return out;
  }

} // anonymous namespace


int main()
{
  int wrong = 0;
  std::set<std::string> seen{ "help" };
  std::deque<std::string> waiting{ "help" };
  while (! waiting.empty()) {
    auto name = waiting.front();
    waiting.pop_front();
    auto text = pl4g::manual::lookup(name);
    if (text.size() > limit) [[unlikely]] {
      std::print(stderr, "'{}' is {} bytes\n", name, text.size());
      ++wrong;
    }
    for (const auto& one : mentioned(text)) {
      if (! pl4g::manual::exists(one)) [[unlikely]] {
        std::print(stderr, "'{}' mentions '{}', which is no node\n", name, one);
        ++wrong;
      } else if (seen.insert(one).second)
        waiting.push_back(one);
    }
  }
  for (auto one : pl4g::manual::names())
    if (! seen.contains(std::string(one))) [[unlikely]] {
      std::print(stderr, "'{}' is not reached from 'help'\n", one);
      ++wrong;
    }
  // A name that is not there, close to one that is, and one close to none.
  if (auto answer = pl4g::manual::lookup("std.strin"); answer.find("'std.string'") == std::string_view::npos
                                                       || pl4g::manual::exists("std.strin")) [[unlikely]] {
    std::print(stderr, "a near miss is answered with: {}", answer);
    ++wrong;
  }
  if (auto answer = pl4g::manual::lookup("qqqqqqqqqqqqqqqqqqqq"); answer.find("Did you mean") != std::string_view::npos)
      [[unlikely]] {
    std::print(stderr, "a far miss is answered with: {}", answer);
    ++wrong;
  }
  if (wrong > 0) [[unlikely]]
    return 1;
  std::print("{} nodes, every one reached from 'help'\n", seen.size());
  return 0;
}
