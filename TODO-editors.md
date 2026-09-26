Editor support: what is open
============================

`editors/nvim` is the one configuration there is, and `tree-sitter-pl4g/queries`
is what it and the compiler both read.  What is missing from either belongs here.

[ ] an indentation query, so that an editor knows a line ending in `:` opens a block.  What there is instead is the indent of the
    line before, which is right inside a block and wrong at the start of one -- so every block begins with a `>>`.  Neovim reads
    `queries/pl4g/indents.scm` for this and so does Helix; the difficulty is that the line being typed is not yet part of any
    block, so what the query has to say something about is the line above and the shape it opened.  A wrong answer is worse than
    none, which is why there is none yet.

[ ] a `folds.scm` that folds a match's arms.  It folds a definition and a block today, which is every block the layout makes, but
    an arm of a `match` is not one of those: its body is a block and the arm itself -- the pattern and the body together -- is
    what a reader wants to put away.

[x] read the compiler's diagnostics in the editor.  Done on 2026-09-26, and not through `errorformat`: `pypl4g lsp` is a language
    server and the compiler is what answers it, so the diagnostics arrive as you type with the numbers and the words and the places
    the compiler gives them.  `:make` with an `errorformat` would still be worth having for a build of many files at once.

[ ] hover shows a reference the way the compiler writes one, `ptr<mut Init>`, and not the way a program writes one, `&mut Init`.
    It is the entry in TODO-pypl4g.md about the renderer, and this is where it became something a reader sees rather than something
    only a diagnostic said.

[ ] the rest of what a language server can be asked.  It answers diagnostics, an outline, what a name is and where it was defined.
    What it does not answer yet: every use of a name (`references`), renaming one, completing one, and formatting a file -- the last
    being a thing the language has no statement about yet, since nothing says what the one true layout of a program is.

[ ] say what a name is without checking the whole file again.  Every keystroke recompiles from the beginning, which is a few
    milliseconds and is why it is allowed to be that simple.  A file that is ten times larger than anything here would want the
    analysis cached per definition, which is the point at which this stops being the compiler run twice.

[ ] a configuration for an editor that is not Neovim.  Helix reads the same queries with a `languages.toml` entry, Emacs has
    `treesit` and wants the capture names mapped to its own faces, and Zed wants an extension.  None of them needs anything of the
    grammar that is not already there, which is the point of the queries being where they are.

[ ] a highlight for a name the program defined against one it only mentioned.  `queries/locals.scm` is what tree-sitter has for
    that, and with it an editor can tell a definition from a use and rename by scope.  Nothing here needs it yet.
