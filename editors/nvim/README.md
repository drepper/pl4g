Neovim configuration for PL4G
=============================

This directory is a Neovim package.  It holds no copy of anything: the parser and
the queries in it are links to `tree-sitter-pl4g` beside it, so an editor reads
the same grammar the compiler does and neither can fall behind the other.

| Path | What it is |
|---|---|
| `ftdetect/pl4g.lua` | what a `.pl4g` file is |
| `ftplugin/pl4g.lua` | how one is edited, and the grammar turned on |
| `parser/pl4g.so` | → `tree-sitter-pl4g/pl4g.so`, which `bin/pl4g-grammar` builds |
| `queries/pl4g/highlights.scm` | → the grammar's own, what to colour |
| `queries/pl4g/folds.scm` | → the grammar's own, what folds |

Neovim 0.11 or later, because the committed parser is written for tree-sitter's
ABI 15 and that is the first version that reads one.  Built and used with 0.12.

Build the parser first
----------------------

The shared library is a build product and is not committed, so the link points
at nothing until it is made:

```sh
bin/pl4g-grammar          # tree-sitter generate, then tree-sitter build
```

Without it everything here still works except the colour, and opening a file
says so once rather than leaving a reader wondering.

How to use it
-------------

**If your configuration uses `lazy.nvim`** -- kickstart.nvim and most others do
-- then name this directory to it and nothing else will work:

```lua
{ dir = "/path/to/pl4g/editors/nvim", lazy = false }
```

`lazy.nvim` sets `packpath` to the Neovim runtime and rebuilds `runtimepath` from
its own list of plugins, both to save a few milliseconds of startup
(`performance.reset_packpath` and `performance.rtp.reset`, on by default).  So a
package under `pack/*/start/` is never loaded and a path added with `--cmd` is
thrown away again, both without a word said.  Whether that is happening is one
line:

```vim
:echo &packpath
```

Just the runtime, with no `~/.config/nvim` in it, means the manager owns these
paths and only a spec it reads will do.  `lazy = false` and not `ft = "pl4g"`,
which is what a plugin for one language usually says: a lazy-loaded package is on
the runtime path only once something has decided the file is of this type, and
what decides that is in the package.  It is four files and three links, so there
is nothing to defer.

**Where nothing owns those paths, a package needs no manager at all:**

```sh
mkdir -p ~/.config/nvim/pack/pl4g/start
ln -s /path/to/pl4g/editors/nvim ~/.config/nvim/pack/pl4g/start/pl4g
```

A link rather than a copy, so that pulling the project brings the grammar and
the queries with it.

**To try it on one file**, with your own configuration left out of it:

```sh
nvim --clean --cmd 'set runtimepath+=/path/to/pl4g/editors/nvim' program.pl4g
```

`--clean` is what makes this worth running: it answers "is the package all right"
rather than "is the package all right together with everything else".  Without it
the same command tells a `lazy.nvim` user nothing, for the reason above.

**And in a session that is already running**, whatever the configuration:

```vim
:set runtimepath+=/path/to/pl4g/editors/nvim
:runtime! ftdetect/*.lua
:edit
```

The middle line is the one that is easy to leave out: adding to the runtime path
does not go back and source what is in it, so without it nothing knows what a
`.pl4g` file is yet.

`nvim-treesitter` is not wanted and is not in the way: what it installs is
parsers and queries, and both are here already.

**However it was installed, this is how to see that it was:**

```vim
:echo nvim_get_runtime_file('lua/pl4g/health.lua', v:true)
```

An empty list means Neovim cannot see the package, and nothing below will work
until it can.

What it does
------------

- **A `.pl4g` file is of type `pl4g`.**  The suffix is the whole of the question:
  the language has no shebang line, a program of it being something a generator
  writes rather than something a system runs.
- **Colour from the grammar**, which is `vim.treesitter.start()` and the queries
  linked above.  `:Inspect` says what any piece of a line was captured as, and
  `:InspectTree` shows the tree the grammar made.
- **Indentation of four spaces and never a tab.**  Indentation is measured in
  characters, and a tab in it is an error the compiler reports (2103), so a tab
  is never what is wanted here.  One that is there already is not shown -- what a
  window shows is the window's business and this package sets nothing of it, so
  `:set list` is yours to type.
- **The comment markers**: `※` for a remark and `※※` for one that belongs to what
  follows it.  So `gcc` and a comment continued onto the next line both write the
  glyph, and neither needs typing.
- **A name as one word.**  `⎕sc@mmap` and `x'` are single names, which is what a
  word-wise motion and a search for the word under the cursor now treat them as.

When nothing is coloured
------------------------

Run **`:checkhealth pl4g`** in a window with the file open.  Six things have to
hold and each of them fails looking exactly like the others from the outside, so
the check says which one it was -- except the first, where the check is one of the
things that is not there:

| What it says | What to do |
|---|---|
| `No healthcheck found for "pl4g" plugin` | the package is not on the runtime path, so not even the check is there.  With `lazy.nvim` this is the usual answer: `:echo &packpath`, and see above |
| nothing says what a `.pl4g` file is | the package is on the path but its `ftdetect` never ran: `filetype on` |
| no `parser/pl4g.so` on the runtime path | run `bin/pl4g-grammar`; the link points at what it builds |
| the parser does not load | Neovim 0.11 or later reads the committed parser; an older one does not |
| the query does not compile | the parser and the queries are out of step: `bin/pl4g-grammar` again |
| no open buffer is coloured | `filetype plugin on`, without which no ftplugin runs |
| the colour scheme paints most groups as ordinary text | nothing is wrong: see below |

**The last is the usual answer.**  Neovim's own default colour scheme gives
`Type`, `Number`, `Operator` and the brackets the ordinary foreground and makes a
keyword bold and nothing more, so a file coloured perfectly well looks almost
plain: a comment is grey, a string green, a function name blue, and everything
else is the colour of the page.  Try `:colorscheme habamax`, which ships with
Neovim, and the same file arrives in six colours.

What each piece of a line was captured as is `:Inspect` with the cursor on it,
and the tree the grammar made is `:InspectTree`.  Between them and the health
check there is nothing about this package that has to be guessed at.

What it does not do
-------------------

- **It changes nothing about the window.**  Everything it sets is set for the
  buffer, so nothing it does outlives the file: open another one in the same
  window and the window is as it was.  What a reader has decided about folding,
  about what is shown and about colours is left decided.

- **It does not fold anything by itself.**  How a file is folded is the reader's
  business, and a package that decided would be deciding for every file.  The
  query is here, so two lines in your own configuration are all it takes:

  ```lua
  vim.wo.foldmethod = "expr"
  vim.wo.foldexpr = "v:lua.vim.treesitter.foldexpr()"
  ```

- **It does not work out the indent of a new line.**  A line ending in `:` opens
  a block and the line after it belongs one step further in; nothing here knows
  that, so what a new line gets is the indent of the one before it and the rest
  is `>>`.  `TODO-editors.md` has the entry.
- **There is no language server**, so nothing here completes a name, renames one
  or shows a diagnostic as you type.  What there is, is the compiler: `:make`
  with `makeprg` set to it reads its diagnostics if you give it an `errorformat`,
  and that is not here yet either.
