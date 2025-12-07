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

-- Modules
local history = require("anya.history")

-- Get current buffer content as string
local function get_buffer_content()
  local lines = vim.api.nvim_buf_get_lines(0, 0, -1, false)
  return table.concat(lines, "\n")
end

-- Set buffer content from string
local function set_buffer_content(content)
  local lines = vim.split(content, "\n", { plain = true })
  vim.api.nvim_buf_set_lines(0, 0, -1, false, lines)
end

-- Send message function
local function send_message()
  local conversation = require("anya.conversation")

  -- Stop navigation if active
  if history.is_navigating() then
    history.stop_navigation()
  end

  conversation.send_message()
end

-- Navigate to previous (older) prompt in history
local function history_previous()
  local current_content = get_buffer_content()

  -- Start navigation if not already navigating
  if not history.is_navigating() then
    history.start_navigation(current_content)
  end

  local prev_prompt = history.navigate_previous()
  if prev_prompt then
    set_buffer_content(prev_prompt)
  end
end

-- Navigate to next (newer) prompt in history
local function history_next()
  if not history.is_navigating() then
    return
  end

  local next_prompt = history.navigate_next()
  if next_prompt then
    set_buffer_content(next_prompt)
  end
end

-- Stop history navigation when entering insert mode
local function on_insert_enter()
  if history.is_navigating() then
    history.stop_navigation()
  end
end

-- Set up autocommands
local augroup = vim.api.nvim_create_augroup("AnyaPromptHistory", { clear = true })
vim.api.nvim_create_autocmd("InsertEnter", {
  group = augroup,
  buffer = 0,
  callback = on_insert_enter,
  desc = "Stop history navigation when entering insert mode",
})

-- History navigation keymaps (normal mode only to avoid conflicts)
vim.keymap.set("n", "<C-p>", history_previous, { buffer = true, desc = "Previous prompt in history" })
vim.keymap.set("n", "<C-n>", history_next, { buffer = true, desc = "Next prompt in history" })

-- Optional: Also allow history navigation in insert mode with different keys
vim.keymap.set("i", "<Up>", function()
  history_previous()
end, { buffer = true, desc = "Previous prompt in history" })

vim.keymap.set("i", "<Down>", function()
  history_next()
end, { buffer = true, desc = "Next prompt in history" })

-- Cancel agent response with Ctrl+C
vim.keymap.set("n", "<C-c>", function()
  vim.cmd("Anya cancel")
end, { buffer = true, desc = "Cancel agent response" })

vim.keymap.set("i", "<C-c>", function()
  vim.cmd("Anya cancel")
end, { buffer = true, desc = "Cancel agent response" })
