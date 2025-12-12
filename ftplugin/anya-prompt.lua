-- Filetype plugin for anya-prompt buffer
-- Configures the prompt input buffer

-- Disable treesitter for prompt buffer to improve typing performance
-- vim.treesitter.language.register("markdown", "anya-prompt")

-- Buffer-local options
vim.opt_local.wrap = true
vim.opt_local.linebreak = true
vim.opt_local.number = false
vim.opt_local.relativenumber = false
vim.opt_local.signcolumn = "no"
vim.opt_local.spell = false
vim.opt_local.winbar = ""
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

-- Cancel agent response with Ctrl+C
vim.keymap.set("n", "<C-c>", function()
  vim.cmd("Anya cancel")
end, { buffer = true, desc = "Cancel agent response" })

vim.keymap.set("i", "<C-c>", function()
  vim.cmd("Anya cancel")
end, { buffer = true, desc = "Cancel agent response" })

-- Handle 1 and 2 key presses for edit responses
-- If there's a pending edit, respond to it
-- Otherwise, allow normal vim behavior (no-op for numbers)
vim.keymap.set("n", "1", function()
  local edit_view = require("anya.edit_view")
  if not edit_view.handle_keypress_any_edit("1") then
    -- No pending edit to respond to, allow normal behavior
    -- (1 key does nothing in prompt buffer)
  end
end, { buffer = true, desc = "Apply pending edit" })

vim.keymap.set("n", "2", function()
  local edit_view = require("anya.edit_view")
  if not edit_view.handle_keypress_any_edit("2") then
    -- No pending edit to respond to, allow normal behavior
    -- (2 key does nothing in prompt buffer)
  end
end, { buffer = true, desc = "Reject pending edit" })

-- Highlight @filepath references and /commands using extmarks (works with treesitter)
vim.api.nvim_set_hl(0, "AnyaFileRef", { link = "Constant", default = true })
vim.api.nvim_set_hl(0, "AnyaSlashCommand", { link = "Special", default = true })

local highlight_ns = vim.api.nvim_create_namespace("anya_highlights")
local bufnr = vim.api.nvim_get_current_buf()

local highlight_timer = nil
local highlight_pending = false

local function highlight_refs()
  vim.api.nvim_buf_clear_namespace(bufnr, highlight_ns, 0, -1)
  local lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)

  for lnum, line in ipairs(lines) do
    local line_idx = lnum - 1
    local pos = 1

    -- Find all @filepath references first
    while true do
      local start_col, end_col = line:find("@[A-Za-z0-9_.~/-]+", pos)
      if not start_col then
        break
      end

      vim.api.nvim_buf_add_highlight(bufnr, highlight_ns, "AnyaFileRef", line_idx, start_col - 1, end_col)
      pos = end_col + 1
    end

    -- Find /commands (simpler check without tracking ranges)
    pos = 1
    while true do
      local start_col, end_col = line:find("/[A-Za-z]+", pos)
      if not start_col then
        break
      end

      -- Check if at start of line or preceded by space
      local preceded_ok = start_col == 1 or line:sub(start_col - 1, start_col - 1) == " "
      -- Check if followed by end of line or space
      local followed_ok = end_col == #line or line:sub(end_col + 1, end_col + 1) == " "

      if preceded_ok and followed_ok then
        vim.api.nvim_buf_add_highlight(bufnr, highlight_ns, "AnyaSlashCommand", line_idx, start_col - 1, end_col)
      end

      pos = end_col + 1
    end
  end

  highlight_pending = false
end

local function schedule_highlight()
  if highlight_timer then
    highlight_timer:stop()
    highlight_timer = nil
  end

  if not highlight_pending then
    highlight_pending = true
    highlight_timer = vim.defer_fn(function()
      if vim.api.nvim_buf_is_valid(bufnr) then
        highlight_refs()
      end
    end, 100) -- Debounce for 100ms
  end
end

vim.api.nvim_create_autocmd({ "TextChanged", "TextChangedI" }, {
  buffer = bufnr,
  callback = schedule_highlight,
  desc = "Schedule @filepath and /command reference highlighting",
})

-- Track leaving/entering prompt for navigation
vim.api.nvim_create_autocmd("WinLeave", {
  buffer = bufnr,
  callback = function()
    vim.g.anya_left_anya_win = true
  end,
  desc = "Track leaving Anya prompt window",
})

vim.api.nvim_create_autocmd("WinEnter", {
  buffer = bufnr,
  callback = function()
    vim.g.anya_left_anya_win = false
  end,
  desc = "Track entering Anya prompt window",
})

-- Navigation from prompt float: <C-w>k goes to chat, <C-w>h goes to code
vim.keymap.set("n", "<C-w>k", function()
  require("anya.float_focus").focus_chat()
end, { buffer = true, desc = "Focus chat window" })

vim.keymap.set("n", "<C-w><C-k>", function()
  require("anya.float_focus").focus_chat()
end, { buffer = true, desc = "Focus chat window" })

vim.keymap.set("n", "<C-w>h", function()
  -- Find chat window and use it to navigate left
  local chat_win = nil
  for _, win in ipairs(vim.api.nvim_list_wins()) do
    if vim.api.nvim_win_is_valid(win) then
      local buf = vim.api.nvim_win_get_buf(win)
      local ft = vim.api.nvim_buf_get_option(buf, "filetype")
      if ft == "anya-chat" then
        chat_win = win
        break
      end
    end
  end
  
  if chat_win then
    vim.api.nvim_set_current_win(chat_win)
    pcall(vim.cmd, "wincmd h")
  end
end, { buffer = true, desc = "Navigate left to code window" })

vim.keymap.set("n", "<C-w><C-h>", function()
  -- Find chat window and use it to navigate left
  local chat_win = nil
  for _, win in ipairs(vim.api.nvim_list_wins()) do
    if vim.api.nvim_win_is_valid(win) then
      local buf = vim.api.nvim_win_get_buf(win)
      local ft = vim.api.nvim_buf_get_option(buf, "filetype")
      if ft == "anya-chat" then
        chat_win = win
        break
      end
    end
  end
  
  if chat_win then
    vim.api.nvim_set_current_win(chat_win)
    pcall(vim.cmd, "wincmd h")
  end
end, { buffer = true, desc = "Navigate left to code window" })

-- Also map bare <C-h>, <C-j>, <C-k>, <C-l> for users who have those mapped
vim.keymap.set("n", "<C-h>", function()
  -- Find chat window and use it to navigate left
  local chat_win = nil
  for _, win in ipairs(vim.api.nvim_list_wins()) do
    if vim.api.nvim_win_is_valid(win) then
      local buf = vim.api.nvim_win_get_buf(win)
      local ft = vim.api.nvim_buf_get_option(buf, "filetype")
      if ft == "anya-chat" then
        chat_win = win
        break
      end
    end
  end
  
  if chat_win then
    vim.api.nvim_set_current_win(chat_win)
    pcall(vim.cmd, "wincmd h")
  end
end, { buffer = true, desc = "Navigate left to code window" })

vim.keymap.set("n", "<C-k>", function()
  require("anya.float_focus").focus_chat()
end, { buffer = true, desc = "Focus chat window" })

-- Initial highlight
highlight_refs()
