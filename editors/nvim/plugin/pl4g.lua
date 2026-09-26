-- Turning the language server on.
--
-- `vim.lsp.enable` says "start the server called `pl4g` for the files it says it
-- is for", and the configuration it reads is `lsp/pl4g.lua` beside this file.
-- Nothing starts until a `.pl4g` file is opened.
--
-- It is here rather than in the ftplugin because enabling is done once for the
-- session, not once per file, and because a `plugin/` file is what Neovim sources
-- once at startup.

if vim.fn.has("nvim-0.11") == 1 and vim.lsp and vim.lsp.enable then
  vim.lsp.enable("pl4g")
end
