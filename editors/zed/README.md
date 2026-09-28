The PL4G extension for Zed
==========================

Both halves of what an editor needs, and neither of them a copy: the grammar is
the one in `tree-sitter-pl4g`, the colouring query is a link to the one the
compiler's own diagnostics are coloured with, and the language server is
`bin/pypl4g lsp`, which is the compiler.

| Path | What it is |
|---|---|
| `extension.toml` | what this is, which grammar it builds and which server it starts |
| `languages/pl4g/config.toml` | the suffix, the comment marker, the indentation and the brackets |
| `languages/pl4g/highlights.scm` | → the grammar's own, what to colour |
| `languages/pl4g/outline.scm` | what the outline panel shows |
| `languages/pl4g/brackets.scm` | which bracket closes which |
| `languages/pl4g/overrides.scm` | where the ordinary rules do not hold: a comment, a string |
| `src/lib.rs`, `Cargo.toml` | the command that starts the server, which is all Zed will read from a file |

Installing it
-------------

```
zed: extensions  →  Install Dev Extension  →  choose editors/zed
```

Zed builds two things when it installs this: the WebAssembly the directory above
compiles to, which wants Rust and the `wasm32-wasip2` target
(`rustup target add wasm32-wasip2`), and the grammar, which it fetches from the
project's repository.

**The grammar comes from a commit, not from the tree.**  Zed clones the
repository at the revision `extension.toml` names, so a change to `grammar.js`
reaches the editor only once it has been committed, pushed, and the revision
bumped:

```sh
bin/pl4g-grammar          # regenerate the parser
git commit ...            # what Zed will fetch
git push
bin/pl4g-zed-rev          # writes the revision into extension.toml
```

A test refuses a revision whose grammar is not the one in the tree, so forgetting
that is a failing test rather than an editor quietly reading a grammar the project
no longer has.  The Neovim package has no such step -- it reads the working tree --
and that difference is the one thing the two configurations do not share.

**The repository has to be one your git can read.**  This project's is private, so
`extension.toml` names it the way a checkout of it does -- `git@github.com:...`,
which git answers with your key.  Where that is not how you reach it, point the
line at your own checkout instead:

```toml
repository = "file:///path/to/pl4g"
```

A local path is always complete and always current, which is what makes it the
answer when the remote is unreachable; it is not committed because it is true of
one machine.  What tells you this went wrong is `grammars/pl4g` beside this file
holding an empty repository, and Zed colouring nothing.

The language server
-------------------

`bin/pypl4g lsp` is the compiler speaking the Language Server Protocol: what is
wrong with the file as you type, an outline, what a name is, and where it was
defined.  The extension looks for the compiler in three places, in this order:

1. what the settings name, for a reader who has said which compiler to use:

   ```json
   { "lsp": { "pl4g": { "binary": { "path": "/somewhere/pl4g/bin/pypl4g" } } } }
   ```

2. `bin/pypl4g` of the project the file is in, which is what is wanted whenever
   the file is in a checkout of the language: a program is then checked by the
   compiler it is written beside;
3. `pypl4g` on the path, for a file that belongs to no such tree.

What it does not do
-------------------

- **No indentation query.**  A line ending in `:` opens a block and the line after
  it belongs one step further in; nothing here knows that, which is the same gap
  the Neovim package has and the same entry in `TODO-editors.md`.
- **Nothing is themed for this language in particular.**  The captures are
  tree-sitter's own names, which Zed resolves against the theme by dropping the
  last part until something matches -- so `@keyword.conditional` is coloured as a
  keyword, and a name a theme has never heard of is left as ordinary text.
