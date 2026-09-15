"""The build attributes a RISC-V image carries, and the one this compiler writes.

An image on this architecture says what it was built for, in a section of its
own that takes no room when the program runs.  It has to: the base is small and
everything else is an extension, so "a RISC-V binary" says almost nothing about
what a processor must have to run it, and there is no instruction a program in
user mode can ask the processor with.  What a reader has instead is this -- a
debugger working out which instructions to expect, a linker checking that two
objects were built for the same machine, a distribution checking that a package
will run on what it ships for.

**The format** is the one ARM invented for the same job and this architecture
adopted.  A byte saying which format, then sub-sections each belonging to a
vendor, each holding sub-sub-sections each covering a scope:

```
'A'
  <u32> length of this sub-section, counting these four bytes
  "riscv\\0"                        which vendor's attributes these are
    <u8>  1                        the scope: the whole file
    <u32> length of this sub-sub-section, counting the byte and these four
      <uleb128> tag
      <value>                      a number for an even tag, text for an odd one
      ...
```

The lengths count themselves, which is what lets a reader that does not know a
vendor or a tag step over it rather than give up -- and is why they are written
after the buffer they measure is built rather than worked out in advance.

**What is written is one tag**, `Tag_RISCV_arch`, whose value is the ISA string
in normalized form: every extension spelled out, in the architecture's order,
each with the version it is at.  The others -- the stack alignment, whether
unaligned access is fast, which privileged specification -- are either things
this compiler does not vary or things about the system rather than the program,
and writing a value nobody chose would be stating something nobody said.
"""

from __future__ import annotations

from typing import Final

#: What the section is called and what kind of section it is.  The type is the
#: architecture's own, in the range the format leaves to each processor.
SECTION: Final[str] = ".riscv.attributes"
SHT_RISCV_ATTRIBUTES: Final[int] = 0x70000003

#: The one format there is, named by the letter the format begins with.
_FORMAT_VERSION: Final[int] = ord("A")

#: Whose attributes these are.  A reader that does not know the name steps over
#: the whole sub-section, which is what its length is for.
_VENDOR: Final[bytes] = b"riscv\x00"

#: The scope a sub-sub-section covers.  There are three -- the whole file, one
#: section, one symbol -- and only the first has ever been used.
_TAG_FILE: Final[int] = 1

#: Which attribute.  Odd tags carry text and even ones carry a number, which is
#: how a reader skips a tag it does not know without a table of them.
_TAG_ARCH: Final[int] = 5


def _uleb128(value: int) -> bytes:
    """A number in the variable-length encoding the format uses for tags."""
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def build(arch: str) -> bytes:
    """The whole section, for a program built for *arch*.

    Built from the inside out, each length written once what it measures is
    there -- which is the only way round that cannot get a length wrong.
    """
    attributes = b"".join((_uleb128(_TAG_ARCH), arch.encode("ascii"), b"\x00"))
    # The scope, its length, and what it covers.  The length counts the tag byte
    # and the four bytes of itself.
    scoped = b"".join((bytes((_TAG_FILE,)),
                       (len(attributes) + 5).to_bytes(4, "little"),
                       attributes))
    # The vendor's whole sub-section, whose length likewise counts itself.
    vendored = b"".join(((len(scoped) + len(_VENDOR) + 4).to_bytes(4, "little"),
                         _VENDOR, scoped))
    return b"".join((bytes((_FORMAT_VERSION,)), vendored))
