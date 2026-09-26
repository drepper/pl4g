-- The language server, which is the compiler.
--
-- Neovim reads this file for the configuration of a server called `pl4g`:
-- `lsp/<name>.lua` on the runtime path is the form it has had since 0.11, and it
-- needs no plugin and no `nvim-lspconfig`.  What turns it on is
-- `vim.lsp.enable("pl4g")`, which `plugin/pl4g.lua` beside this does.
--
-- The compiler is found beside this file rather than on the PATH: what is wanted
-- is the compiler of the project this configuration came with, and a `pypl4g`
-- installed somewhere else would be a different one.  The path is resolved
-- through its links, so that it is right whether this directory was named
-- directly or reached through a link in `pack/*/start/`.

local here = vim.uv.fs_realpath(debug.getinfo(1, "S").source:sub(2))
local project = vim.fs.dirname(vim.fs.dirname(vim.fs.dirname(vim.fs.dirname(here))))

return {
  cmd = { project .. "/bin/pypl4g", "lsp" },
  filetypes = { "pl4g" },
  -- What the project is, as far as the server is concerned: the directory a
  -- module is looked for in is the directory of the file that imports it, so a
  -- single file is a whole project and nothing has to be found.  A marker is
  -- named anyway, so that one window's worth of files shares one server.
  root_markers = { ".git", "CLAUDE.md" },
  -- Counted in characters, which is what the compiler counts; the server offers
  -- all three and takes the first of them this editor names.
  capabilities = {
    general = { positionEncodings = { "utf-8", "utf-16" } },
  },
}
