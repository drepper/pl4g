-- `:checkhealth pl4g` -- why a PL4G file is not coloured.
--
-- Five things have to hold, and each of them fails in a way that looks exactly
-- like the others from the outside: the file is not of this type, the package is
-- not on the runtime path, the parser was never built, the queries do not
-- compile, or everything works and the colour scheme paints almost nothing.  The
-- last is the one nothing else would tell you: Neovim's own default scheme gives
-- `Type`, `Number` and `Operator` the ordinary foreground, so a file coloured
-- perfectly well looks plain.

local M = {}

--- What a highlight group amounts to once its links are followed.
--- @param name string the group to resolve
--- @return table|nil the attributes it sets, or nothing where it sets none
local function resolved(name)
  local seen = 0
  local found = vim.api.nvim_get_hl(0, { name = name, link = true })
  while found and found.link and seen < 8 do
    found = vim.api.nvim_get_hl(0, { name = found.link, link = true })
    seen = seen + 1
  end
  return found
end

--- Whether a group says anything a reader would see, beyond the ordinary text.
--- @param name string the group to ask about
--- @param ordinary integer|nil the foreground of ordinary text
--- @return boolean
local function stands_out(name, ordinary)
  local found = resolved(name)
  if not found or vim.tbl_isempty(found) then
    return false
  end
  if found.bold or found.italic or found.underline or found.undercurl
      or found.reverse or found.standout or found.strikethrough or found.bg then
    return true
  end
  return found.fg ~= nil and found.fg ~= ordinary
end

--- The whole of the check, run by `:checkhealth pl4g`.
function M.check()
  vim.health.start("pl4g")

  -- The suffix, which is what says a file is of this language at all.
  if vim.filetype.match({ filename = "a.pl4g" }) == "pl4g" then
    vim.health.ok("a `.pl4g` file is of type `pl4g`")
  else
    vim.health.error("nothing says what a `.pl4g` file is", {
      "the package is not on the runtime path; see editors/nvim/README.md",
    })
    return
  end

  -- The parser, which is a build product and so the thing most often missing.
  -- A link pointing at nothing is not found at all, which is the same answer as
  -- never having built it and wants the same thing done about it.
  local parsers = vim.api.nvim_get_runtime_file("parser/pl4g.so", true)
  if #parsers == 0 then
    vim.health.error("no `parser/pl4g.so` on the runtime path", {
      "run `bin/pl4g-grammar` in the project, which builds it",
      "the link in the package points at what it builds, and is not it",
    })
    vim.health.info("what is below needs the grammar, so it is not asked")
    return
  end
  local ok, why = pcall(vim.treesitter.language.add, "pl4g")
  if not ok then
    vim.health.error(("`%s` does not load: %s"):format(parsers[1], why), {
      "a parser this Neovim cannot read: the committed one wants 0.11 or later",
      "or one built by a tree-sitter too new for it: `bin/pl4g-grammar` again",
    })
    vim.health.info("what is below needs the grammar, so it is not asked")
    return
  end
  vim.health.ok(("the grammar loads, from `%s`"):format(parsers[1]))

  -- The queries, which are what say what to colour.
  for _, what in ipairs({ "highlights", "folds" }) do
    local where = vim.api.nvim_get_runtime_file("queries/pl4g/" .. what .. ".scm", true)
    if #where == 0 then
      vim.health.warn(("no `queries/pl4g/%s.scm` on the runtime path"):format(what))
    else
      local ok, why = pcall(vim.treesitter.query.get, "pl4g", what)
      if ok then
        vim.health.ok(("the %s query compiles"):format(what))
      else
        vim.health.error(("the %s query does not compile: %s"):format(what, why), {
          "the queries and the parser are out of step: run `bin/pl4g-grammar`",
        })
      end
    end
  end

  -- The files of this type that are open.  Not the current buffer: this check
  -- runs in a buffer of its own, which is never one of them.
  local open, coloured = 0, 0
  for _, buf in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_is_loaded(buf) and vim.bo[buf].filetype == "pl4g" then
      open = open + 1
      if vim.treesitter.highlighter.active[buf] then
        coloured = coloured + 1
      end
    end
  end
  if open == 0 then
    vim.health.info("open a `.pl4g` file and run this again to check a buffer")
  elseif coloured == open then
    vim.health.ok(("%d of %d open `.pl4g` buffers are coloured by the grammar")
      :format(coloured, open))
  else
    vim.health.error(("%d of %d open `.pl4g` buffers are coloured by the grammar")
      :format(coloured, open), {
      "`filetype plugin on`, without which the ftplugin never runs",
      "or something else called `vim.treesitter.stop()` on the buffer",
    })
  end

  -- And the colour scheme, which is where a working configuration still looks
  -- like a broken one.
  local ordinary = (resolved("Normal") or {}).fg
  local plain = {}
  for _, group in ipairs({ "@keyword", "@type", "@function", "@variable",
                           "@string", "@number", "@comment", "@operator",
                           "@punctuation.bracket", "@attribute" }) do
    if not stands_out(group, ordinary) then
      plain[#plain + 1] = group
    end
  end
  if #plain == 0 then
    vim.health.ok("the colour scheme paints every group the queries use")
  elseif #plain < 5 then
    vim.health.info(("the colour scheme paints these as ordinary text: %s")
      :format(table.concat(plain, " ")))
  else
    vim.health.warn(("the colour scheme paints most groups as ordinary text: %s")
      :format(table.concat(plain, " ")), {
      "nothing is wrong with the grammar; this is what the scheme says",
      "Neovim's own default scheme is one of these -- try `:colorscheme habamax`",
    })
  end
end

return M
