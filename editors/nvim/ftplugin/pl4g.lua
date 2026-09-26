-- What editing a PL4G file is like, and the grammar that colours it.
--
-- Everything here is per buffer: an ftplugin runs for each file of the type, a
-- setting made globally would be made for every other file too, and a window's
-- own settings -- what it shows, how it folds -- are the reader's and are left
-- alone.
--
-- A glyph inside a string is written as an escape, the way the compiler's Python
-- writes one: what a reader of this file needs to see is which option is being
-- set, and a line of glyphs is hard to read and harder to search for.

-- Indentation is measured in characters, and a tab in it is an error the
-- compiler reports (2103) -- so a tab is never what is wanted here, and the
-- width is the four the language is written in everywhere.
vim.bo.expandtab = true
vim.bo.shiftwidth = 4
vim.bo.softtabstop = 4
vim.bo.tabstop = 4
-- A block is what is indented under the line that opens it, so the indent of
-- the line before is the first guess at the indent of the next.  It is only a
-- guess: nothing here works out that a line ending in `:` opens a block.
vim.bo.autoindent = true

-- The comment markers, most particular first: `※※` belongs to what follows it
-- and `※` is a remark.  `commentstring` is what a commenting command writes and
-- `comments` is what a continued line repeats.
vim.bo.commentstring = "\u{203B} %s"
vim.bo.comments = ":\u{203B}\u{203B},:\u{203B}"

-- A name may hold an apostrophe, and one the compiler provides may hold an `@`
-- (`⎕sc@mmap` is one).  Saying so makes a word-wise motion, or a search for the
-- word under the cursor, treat such a name as the one thing it is.  The `⎕` that
-- begins such a name needs no saying and cannot be said: every character above
-- 255 counts as a word character to this editor, and this option speaks only
-- about the ones below.
vim.opt_local.iskeyword:append({ 39, "@-@" })

-- The grammar, which is a shared library built from `tree-sitter-pl4g` and
-- found through `parser/pl4g.so` beside this file.  It is a build product, so
-- it can be missing; what is lost then is colour, and the message says what to
-- run.  Everything above this line holds either way.
if not pcall(vim.treesitter.start) then
  -- Nothing else colours this language, so what is left is a plain buffer and
  -- one line saying why.
  vim.notify_once(
    "pl4g: no tree-sitter parser; run bin/pl4g-grammar in the project",
    vim.log.levels.WARN)
end

-- What an editor undoes when the file is closed.  A buffer-local option set
-- here is not undone by anything else, so it is said here.
vim.b.undo_ftplugin = table.concat({
  "setlocal expandtab< shiftwidth< softtabstop< tabstop< autoindent<",
  "setlocal commentstring< comments< iskeyword<",
}, " | ")
