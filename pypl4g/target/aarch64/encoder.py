"""The AArch64 encoder.

One word, one loop, which is what the shared fixed-width encoder does.  Nothing
here is specific to this architecture except its instruction width; what is
specific -- the relocations and how their values are stored -- is in ``fixups``.
"""

from __future__ import annotations

from ...mc.fixup import MCFixup
from ...mc.fixedwidth import EncodingError, encode as encode_fixed_width
from ...mc.inst import MCInst
from .desc import INSTRUCTION_SIZE

__all__ = ["EncodingError", "encode"]


def encode(inst: MCInst) -> tuple[bytes, list[MCFixup]]:
    """Encode one instruction into its word and the fixups it leaves behind."""
    return encode_fixed_width(inst, INSTRUCTION_SIZE)
