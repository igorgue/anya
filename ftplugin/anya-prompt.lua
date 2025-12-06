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

-- Send message function
local function send_message()
  local conversation = require("anya.conversation")
  conversation.send_message()
end

-- Map Enter to send message in both normal and insert mode
vim.keymap.set("n", "<CR>", send_message, { buffer = true, desc = "Send message" })
vim.keymap.set("i", "<CR>", function()
  -- Exit insert mode and send message
  vim.cmd("stopinsert")
  send_message()
end, { buffer = true, desc = "Send message" })
