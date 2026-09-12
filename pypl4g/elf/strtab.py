"""String tables.

Strings are shared where one is a suffix of another, which costs a dictionary
lookup and makes the table as small as it can be without any further work.
"""

from dataclasses import dataclass, field


@dataclass(slots=True)
class StringTable:
    """Builds an ELF string table."""

    _data: bytearray = field(default_factory=lambda: bytearray(b"\0"))
    _offsets: dict[str, int] = field(default_factory=lambda: {"": 0})

    def add(self, text: str) -> int:
        """Return the offset of *text*, appending it if it is not there yet."""
        found = self._offsets.get(text)
        if found is not None:
            return found
        encoded = text.encode("utf-8")
        for known, offset in self._offsets.items():
            if known.endswith(text) and known != text:
                shared = offset + len(known.encode("utf-8")) - len(encoded)
                self._offsets[text] = shared
                return shared
        offset = len(self._data)
        self._data += encoded
        self._data += b"\0"
        self._offsets[text] = offset
        return offset

    @property
    def size(self) -> int:
        """The number of bytes the table occupies."""
        return len(self._data)

    def bytes_of(self) -> bytes:
        """The bytes of the table."""
        return bytes(self._data)
