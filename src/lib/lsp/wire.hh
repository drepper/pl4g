// The messages an editor and a server exchange, and how they are framed: a
// port of pypl4g/lsp/wire.py.
//
// JSON-RPC 2.0 with a header before each message, the way HTTP frames a body:
// `Content-Length: N`, a blank line, and then N bytes of JSON.
#pragma once

#include <exception>
#include <string>

#include "lsp/json.hh"


namespace pl4g::lsp {

  // The other end went away, which is how a session ends when it is killed.
  struct Closed : std::exception {
    const char* what() const noexcept override { return "the other end went away"; }
  };


  // One pair of descriptors, carrying framed JSON in both directions.
  class Wire {
  public:
    Wire(int reader, int writer) : reader(reader), writer(writer) {}

    // The next message, an empty object where it was no JSON object, or
    // `Closed` where there will not be one.  A header this does not know is
    // skipped: the only one that has to be understood is the length.
    Json read();
    // Send one message, header and all.
    void write(const Json& message);

  private:
    // The next line, with its end; empty at the end of the input.
    std::string line();
    // Exactly `length` bytes, however many reads that takes.
    std::string exactly(std::size_t length);
    // More of the input into `pending`; false at its end.
    bool more();

    int reader;
    int writer;
    std::string pending;
  };

} // namespace pl4g::lsp
