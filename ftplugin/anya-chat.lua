-- Filetype plugin for anya-chat buffer
-- Configures the chat display buffer

-- Register markdown treesitter for syntax highlighting
vim.treesitter.language.register("markdown", "anya-chat")

-- Buffer-local options
vim.opt_local.wrap = true
vim.opt_local.linebreak = true
vim.opt_local.breakindent = true
vim.opt_local.showbreak = ""
vim.opt_local.number = false
vim.opt_local.relativenumber = false
vim.opt_local.signcolumn = "no"
vim.opt_local.foldmethod = "manual"
vim.opt_local.foldenable = true
vim.opt_local.modifiable = true
vim.opt_local.spell = false
vim.opt_local.conceallevel = 2
vim.opt_local.concealcursor = "nvic"
