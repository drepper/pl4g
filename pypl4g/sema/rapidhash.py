"""The hash a table's keys are hashed with, worked out while compiling.

What the runtime's `pl4g_hash` does (`runtime/io.c`): rapidhash, after Nicolas De
Carli's, seeded.  The compiler needs it for the tables it lays out in the image,
whose entries have to be where a probe made while running looks for them; a test
holds the two to each other.
"""

from __future__ import annotations

from typing import Final

#: The three words the hash is mixed with.
SECRET: Final[tuple[int, int, int]] = (0x2D358DCCAA6C78A5, 0x8BB84B93962EACC9,
                                       0x4B33A62ED433D4A3)

#: Every word is sixty-four bits, and so is every sum and product kept.
_WORD: Final[int] = (1 << 64) - 1


def _mum(a: int, b: int) -> tuple[int, int]:
    """Both halves of the product of two words: the low, then the high."""
    product = a * b
    return product & _WORD, product >> 64


def _mix(a: int, b: int) -> int:
    """The two halves of the product, combined."""
    low, high = _mum(a, b)
    return low ^ high


def _read64(data: bytes, at: int) -> int:
    return int.from_bytes(data[at:at + 8], "little")


def _read32(data: bytes, at: int) -> int:
    return int.from_bytes(data[at:at + 4], "little")


def rapidhash(data: bytes, seed: int) -> int:
    """The hash of *data* with *seed*, as `pl4g_hash` answers it."""
    length = len(data)
    seed = (seed ^ _mix(seed ^ SECRET[0], SECRET[1]) ^ length) & _WORD
    p = 0
    if length <= 16:
        if length >= 4:
            last = length - 4
            a = (_read32(data, 0) << 32) | _read32(data, last)
            delta = (length & 24) >> (length >> 3)
            b = (_read32(data, delta) << 32) | _read32(data, last - delta)
        elif length > 0:
            a = (data[0] << 56) | (data[length >> 1] << 32) | data[length - 1]
            b = 0
        else:
            a = b = 0
    else:
        left = length
        if left > 48:
            see1 = see2 = seed
            while left >= 96:
                seed = _mix(_read64(data, p) ^ SECRET[0], _read64(data, p + 8) ^ seed)
                see1 = _mix(_read64(data, p + 16) ^ SECRET[1],
                            _read64(data, p + 24) ^ see1)
                see2 = _mix(_read64(data, p + 32) ^ SECRET[2],
                            _read64(data, p + 40) ^ see2)
                seed = _mix(_read64(data, p + 48) ^ SECRET[0],
                            _read64(data, p + 56) ^ seed)
                see1 = _mix(_read64(data, p + 64) ^ SECRET[1],
                            _read64(data, p + 72) ^ see1)
                see2 = _mix(_read64(data, p + 80) ^ SECRET[2],
                            _read64(data, p + 88) ^ see2)
                p += 96
                left -= 96
            if left >= 48:
                seed = _mix(_read64(data, p) ^ SECRET[0], _read64(data, p + 8) ^ seed)
                see1 = _mix(_read64(data, p + 16) ^ SECRET[1],
                            _read64(data, p + 24) ^ see1)
                see2 = _mix(_read64(data, p + 32) ^ SECRET[2],
                            _read64(data, p + 40) ^ see2)
                p += 48
                left -= 48
            seed ^= see1 ^ see2
        if left > 16:
            seed = _mix(_read64(data, p) ^ SECRET[2],
                        _read64(data, p + 8) ^ seed ^ SECRET[1])
            if left > 32:
                seed = _mix(_read64(data, p + 16) ^ SECRET[2],
                            _read64(data, p + 24) ^ seed)
        a = _read64(data, p + left - 16)
        b = _read64(data, p + left - 8)
    a ^= SECRET[1]
    b ^= seed
    a, b = _mum(a, b)
    return _mix(a ^ SECRET[0] ^ length, b ^ SECRET[1])


def word_hash(word: int, seed: int) -> int:
    """The hash of a key that is one word, as `pl4g_hash_word` answers it: its
    eight bytes as they are in memory."""
    return rapidhash((word & _WORD).to_bytes(8, "little"), seed)
