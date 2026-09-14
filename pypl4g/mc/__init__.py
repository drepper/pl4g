"""The symbolic assembler.

The representation is built by calling functions in order; it is internal and is
never exposed as a text syntax.  Inline assembly, when it arrives, will be a
parser that drives these same calls, so it can never express anything the
encoder cannot emit.
"""

from __future__ import annotations
