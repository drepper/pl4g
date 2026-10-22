// The manual of PL4G, a node at a time.
//
// The manual is a directed graph of short texts, each with a name: `help` is
// the top, and every other node is reached from it by following the names a
// node mentions, written `[[name]]`.  A node of a topic says what the topic
// holds and names its nodes; none is more than four kilobytes.  The texts are
// made by bin/pl4g-manual -- from share/manual/ and the library's own modules --
// whenever the library is built, and embedded in it, so nothing is read at run
// time and what is served is what the language and its library are.
//
// Standalone: this needs nothing of the compiler, and the compiler nothing of
// this.
#pragma once

#include <span>
#include <string_view>


namespace pl4g::manual {

  // The text of the node `name`, "help" for the top.  Where there is no such
  // node, a text saying so, which names the nodes whose names are close to it
  // where there are any.  The text of a node lives as long as the program; one
  // saying there is no node is the calling thread's until it next asks for a
  // name that is not there.
  std::string_view lookup(std::string_view name);

  // Whether there is a node called `name`.
  bool exists(std::string_view name) noexcept;

  // The names of every node, in order.
  std::span<const std::string_view> names() noexcept;

} // namespace pl4g::manual
