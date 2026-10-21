"""The tables a shared object hands the loader.

A shared object is loaded at an address chosen when it is loaded, so it is
linked at nought and says, in a relocation per word, which of its words hold an
address the load address is added to.  It says what it offers and what it needs
in a symbol table of its own -- the dynamic one, which is the only one a loader
reads -- with a hash table to look a name up by, and it says where all of these
are in the dynamic section, which is what the loader finds first.

Everything here is a pure function of what the writer planned: the bytes are
packed once every address is known, and their sizes are known before any is.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Final, Sequence

from .const import RELA_SIZE, SYM_SIZE, DYN_SIZE

#: A relocation with an addend, and an entry of the dynamic section.
RELA: Final[struct.Struct] = struct.Struct("<QQq")
DYN: Final[struct.Struct] = struct.Struct("<qQ")

assert RELA.size == RELA_SIZE and DYN.size == DYN_SIZE and SYM_SIZE == 24


def elf_hash(name: str) -> int:
    """The System V hash of *name*, which is what a `DT_HASH` table is keyed by."""
    value = 0
    for byte in name.encode("utf-8"):
        value = (value << 4) + byte
        high = value & 0xF0000000
        if high:
            value ^= high >> 24
        value &= ~high & 0xFFFFFFFF
    return value


def hash_size(count: int) -> int:
    """How many bytes the hash table of *count* symbols takes, the null one among
    them: a bucket per symbol, and a chain entry per symbol."""
    return 4 * (2 + 2 * count)


def hash_table(names: Sequence[str]) -> bytes:
    """The hash table of the dynamic symbols called *names*, the null one first.

    As many buckets as symbols, which is a table no search has to walk far in;
    each symbol is put at the head of its bucket's chain, in order.
    """
    count = len(names)
    buckets = [0] * count
    chains = [0] * count
    for index in range(1, count):
        bucket = elf_hash(names[index]) % count
        chains[index] = buckets[bucket]
        buckets[bucket] = index
    return struct.pack("".join(("<", str(2 + 2 * count), "I")), count, count,
                       *buckets, *chains)


@dataclass(frozen=True, slots=True)
class Relocation:
    """One word the loader writes: where, how, which symbol and what to add."""

    offset: int
    kind: int
    symbol: int = 0
    addend: int = 0

    def packed(self) -> bytes:
        """The entry as the table holds it."""
        return RELA.pack(self.offset, (self.symbol << 32) | self.kind, self.addend)


def dynamic_entry(tag: int, value: int) -> bytes:
    """One entry of the dynamic section."""
    return DYN.pack(tag, value)
