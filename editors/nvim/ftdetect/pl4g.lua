-- What a `.pl4g` file is.
--
-- The suffix is the whole of the question: the language has no shebang line and
-- no other way to be recognized, a program of it being something a generator
-- writes rather than something a system runs.
vim.filetype.add({ extension = { pl4g = "pl4g" } })
