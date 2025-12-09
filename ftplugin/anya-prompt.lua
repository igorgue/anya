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

local function highlight_refs()
  vim.api.nvim_buf_clear_namespace(bufnr, highlight_ns, 0, -1)
  local lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)
  for lnum, line in ipairs(lines) do
    -- Track file ref ranges to avoid highlighting commands inside them
    local file_ranges = {}

    -- Highlight @filepath references (e.g., @src/main.lua, @/home/user/file.txt)
    for start_col, end_col in line:gmatch("()@[A-Za-z0-9_.~/-]+()") do
      vim.api.nvim_buf_add_highlight(bufnr, highlight_ns, "AnyaFileRef", lnum - 1, start_col - 1, end_col - 1)
      table.insert(file_ranges, { start_col - 1, end_col - 1 })
    end

    -- Highlight /commands (single word, at start or after space, not inside file refs)
    -- Pattern: start of line or space, then /letters, then end or space
    for start_col, cmd, end_col in line:gmatch("()(/[A-Za-z]+)()") do
      local sc = start_col - 1
      -- Check if at start of line or preceded by space
      local preceded_ok = sc == 0 or line:sub(sc, sc) == " "
      -- Check if followed by end of line or space
      local ec = end_col - 1
      local followed_ok = ec >= #line or line:sub(end_col, end_col) == " "
      -- Check not inside a file ref
      local inside_fileref = false
      for _, range in ipairs(file_ranges) do
        if sc >= range[1] and sc < range[2] then
          inside_fileref = true
          break
        end
      end
      if preceded_ok and followed_ok and not inside_fileref then
        vim.api.nvim_buf_add_highlight(bufnr, highlight_ns, "AnyaSlashCommand", lnum - 1, sc, ec)
      end
    end
  end
end

vim.api.nvim_create_autocmd({ "TextChanged", "TextChangedI" }, {
  buffer = bufnr,
  callback = highlight_refs,
  desc = "Highlight @filepath and /command references",
})
highlight_refs()
