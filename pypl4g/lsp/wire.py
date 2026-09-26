"""The messages an editor and a server exchange, and how they are framed.

The protocol is JSON-RPC 2.0 with a header before each message, the way HTTP
frames a body: `Content-Length: N`, a blank line, and then N bytes of JSON.  It
is done by hand here because the compiler depends on nothing outside the standard
library and this is thirty lines; a package for it would be the first dependency
the project has, and would be one for reading a length and writing a length.
"""

from __future__ import annotations

import json
from typing import BinaryIO, Final, Mapping

#: What every message says about itself, and the one field that matters.
LENGTH: Final[str] = "content-length"

#: The encoding the protocol settled on.  It says `utf-8`, and it once said
#: `utf8`, which some clients still write.
CHARSET: Final[str] = "utf-8"


class Closed(Exception):
    """The other end went away, which is how a session ends when it is killed.

    An editor that stops without saying `exit` leaves the server reading a pipe
    nobody will write to again, and the answer to that is to stop rather than to
    report anything: there is nobody left to report it to.
    """


class Wire:
    """One pair of streams, carrying framed JSON in both directions."""

    def __init__(self, reader: BinaryIO, writer: BinaryIO) -> None:
        self._reader = reader
        self._writer = writer

    def read(self) -> dict[str, object]:
        """The next message, or `Closed` where there will not be one.

        A header this does not know is skipped rather than refused: the protocol
        allows any, and the only one that has to be understood is the length.
        """
        length = -1
        while True:
            line = self._reader.readline()
            if not line:
                raise Closed
            stripped = line.strip()
            if not stripped:
                break
            name, _, value = stripped.decode(CHARSET, "replace").partition(":")
            if name.strip().lower() == LENGTH:
                try:
                    length = int(value.strip())
                except ValueError as exc:
                    raise Closed from exc
        if length < 0:
            raise Closed
        body = self._read_exactly(length)
        try:
            found = json.loads(body.decode(CHARSET))
        except (UnicodeDecodeError, json.JSONDecodeError):
            # A message that is not JSON is not something to answer: an answer
            # names the request it answers and there is no name to be had.
            return {}
        return found if isinstance(found, dict) else {}

    def _read_exactly(self, length: int) -> bytes:
        """Read *length* bytes, however many reads that takes."""
        chunks: list[bytes] = []
        left = length
        while left > 0:
            chunk = self._reader.read(left)
            if not chunk:
                raise Closed
            chunks.append(chunk)
            left -= len(chunk)
        return b"".join(chunks)

    def write(self, message: Mapping[str, object]) -> None:
        """Send one message, header and all."""
        body = json.dumps(message, ensure_ascii=False).encode(CHARSET)
        self._writer.write("".join(("Content-Length: ", str(len(body)),
                                    "\r\n\r\n")).encode(CHARSET))
        self._writer.write(body)
        self._writer.flush()
