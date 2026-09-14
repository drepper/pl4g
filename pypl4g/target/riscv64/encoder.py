"""The RISC-V encoder.

One word, one loop, which is what the shared fixed-width encoder does.  What is
specific to this architecture -- the relocations and the way their values are
scattered through the word -- is in ``fixups``.
"""

from __future__ import annotations

from ...mc.fixedwidth import EncodingError, encode as encode_fixed_width
from ...mc.fixup import MCFixup
from ...mc.inst import MCInst
from .desc import INSTRUCTION_SIZE

__all__ = ["EncodingError", "encode"]


def encode(inst: MCInst) -> tuple[bytes, list[MCFixup]]:
    """Encode one instruction into its word and the fixups it leaves behind."""
    return encode_fixed_width(inst, INSTRUCTION_SIZE)
