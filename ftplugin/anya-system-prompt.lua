-- Filetype plugin for anya-system-prompt buffers
-- Provides a read-only view of the Code agent's system prompt

-- Set buffer-local options
vim.bo.readonly = true
vim.bo.modifiable = false
vim.bo.buftype = "nofile"
vim.bo.bufhidden = "wipe"
vim.bo.buflisted = false

-- Set local leader keybind to re-fetch the system prompt
vim.keymap.set("n", "<localleader>p", function()
  require("anya.system_prompt").show()
end, {
  buffer = true,
  desc = "Refresh Anya system prompt",
  silent = true,
})

-- Add a command to close the buffer easily
vim.keymap.set("n", "q", "<cmd>close<cr>", {
  buffer = true,
  desc = "Close system prompt buffer",
  silent = true,
})
