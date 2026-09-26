"""The compiler as a language server.

`pypl4g lsp` speaks the Language Server Protocol on its standard input and
output, and what answers every question is the compiler itself: the same lexer,
the same parser, the same checker and -- where the file has been saved -- the same
code generation, over the text the editor holds rather than over a file on disk.

**Why the compiler and not a server beside it.**  A server that reimplemented any
of this would be a second statement of what the language is, and the project has
one of those already (the tree-sitter grammar, which is kept honest by a test).
Two would drift, and the one a reader would notice drifting is the one in the
editor: a program it called wrong and the compiler accepted, or the other way
round.  So there is nothing here but the protocol and the places where a span of
the compiler's meets a range of the editor's.

Compare: **rust-analyzer** and **gopls**, each a reimplementation of the front end
built for an editor's needs, which is what a language with a slow batch compiler
has to do; **clangd**, which is the compiler's own front end in a server, as this
is; **zls**, a separate program that parses Zig itself; **pylsp**, which wraps a
pile of tools that each read the file again.  What makes clangd's shape the one to
copy here is that this compiler is already fast enough to run on every keystroke
-- a few milliseconds for a file of the size anything here is -- so there is
nothing an incremental reimplementation would buy.
"""

from __future__ import annotations

from .server import serve

__all__ = ["serve"]
