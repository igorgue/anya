-- Filetype plugin for anya-prompt buffer
-- Configures the prompt input buffer

-- Register markdown treesitter for syntax highlighting
vim.treesitter.language.register("markdown", "anya-prompt")

-- Buffer-local options
vim.opt_local.wrap = true
vim.opt_local.linebreak = true
vim.opt_local.number = false
vim.opt_local.relativenumber = false
vim.opt_local.signcolumn = "no"
vim.opt_local.spell = false
vim.opt_local.modifiable = true
