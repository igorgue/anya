-- Filetype plugin for anya-prompt buffer
-- Configures the prompt input buffer
-- NOTE: <CR> mapping for sending messages is defined here to coexist with completion plugins

-- Disable treesitter for prompt buffer to improve typing performance
-- vim.treesitter.language.register("markdown", "anya-prompt")

-- Buffer-local options
vim.opt_local.wrap = true
vim.opt_local.linebreak = false
vim.opt_local.number = false
vim.opt_local.relativenumber = false
vim.opt_local.signcolumn = "no"
vim.opt_local.spell = true
vim.opt_local.modifiable = true
-- Clear winbar on this window to prevent navic or other plugins from interfering
-- Use both opt.winbar (affects current window) and window option for reliability
vim.opt.winbar = ""
vim.opt_local.showbreak = ""

-- Read config
local anya_config = (function()
  local ok, mod = pcall(require, "anya")
  return ok and mod.config or { start_in_insert = false }
end)()

local bufnr = vim.api.nvim_get_current_buf()

-- Enter insert mode on load if start_in_insert is set
if anya_config.start_in_insert then
  vim.schedule(function()
    if vim.api.nvim_get_current_buf() == bufnr and vim.api.nvim_get_mode().mode ~= "i" then
      vim.cmd("startinsert")
    end
  end)
end

-- Set up autocommands
local augroup = vim.api.nvim_create_augroup("AnyaPromptHistory", { clear = true })

-- Flag to prevent autocmds from stopping navigation during programmatic updates
local navigating_programmatically = false

-- Expose the flag setter for the cycle_history function
_G._anya_set_navigating_programmatically = function(value)
  navigating_programmatically = value
end

vim.api.nvim_create_autocmd("InsertEnter", {
  group = augroup,
  buffer = 0,
  callback = function()
    -- Don't stop navigation if we're programmatically updating
    if navigating_programmatically then
      return
    end
    local history = require("anya.history")
    if history.is_navigating() then
      history.stop_navigation()
    end
  end,
  desc = "Stop history navigation when entering insert mode",
})

-- Stop history navigation when buffer is modified (user starts typing)
vim.api.nvim_create_autocmd("TextChangedI", {
  group = augroup,
  buffer = 0,
  callback = function()
    -- Don't stop navigation if we're programmatically updating
    if navigating_programmatically then
      return
    end
    local history = require("anya.history")
    if history.is_navigating() then
      history.stop_navigation()
    end
  end,
  desc = "Stop history navigation when buffer is modified",
})

-- Dynamically adjust prompt window height based on content
vim.api.nvim_create_autocmd({ "TextChanged", "TextChangedI", "TextChangedP" }, {
  group = augroup,
  buffer = 0,
  callback = function()
    -- Use pcall to handle cases where the RPC channel is busy
    pcall(vim.fn.AnyaRepositionFloats)
  end,
  desc = "Adjust prompt height based on content",
})

-- Highlight @filepath references and /commands using extmarks (works with treesitter)
vim.api.nvim_set_hl(0, "AnyaFileRef", { link = "Constant", default = true })
vim.api.nvim_set_hl(0, "AnyaConvRef", { link = "Function", default = true })
vim.api.nvim_set_hl(0, "AnyaSlashCommand", { link = "Special", default = true })

local highlight_ns = vim.api.nvim_create_namespace("anya_prompt_highlights")

local highlight_timer = nil
local highlight_pending = false
local highlight_scheduled = false

_G.anya_prompt_last_typed_at = _G.anya_prompt_last_typed_at or 0
_G.anya_prompt_typing_grace_ms = _G.anya_prompt_typing_grace_ms or 350

local function mark_prompt_typed()
  _G.anya_prompt_last_typed_at = vim.loop.now()
end

local function highlight_refs()
  if not vim.api.nvim_buf_is_valid(bufnr) then
    highlight_pending = false
    highlight_scheduled = false
    return
  end

  vim.api.nvim_buf_clear_namespace(bufnr, highlight_ns, 0, -1)
  local lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)

  for lnum, line in ipairs(lines) do
    local line_idx = lnum - 1
    local pos = 1

    -- Find #conversation_id mentions first (highlight with AnyaConvRef)
    while true do
      local start_col, end_col = line:find("#[A-Za-z0-9_-]+", pos)
      if not start_col then
        break
      end

      vim.api.nvim_buf_set_extmark(
        bufnr,
        highlight_ns,
        line_idx,
        start_col - 1,
        { end_col = end_col, hl_group = "AnyaConvRef", priority = 200 }
      )
      pos = end_col + 1
    end

    -- Find all @filepath references (highlight with AnyaFileRef)
    -- Skip # patterns to avoid double-highlighting
    pos = 1
    while true do
      -- Use a custom scanner to handle \-escaped spaces in file paths
      local start_col = line:find("@", pos)
      if not start_col then break end
      local end_col = start_col
      local in_escape = false
      for j = start_col + 1, #line do
        local c = line:sub(j, j)
        if in_escape then
          -- Any character after backslash is part of the path
          end_col = j
          in_escape = false
        elseif c == "\\" then
          -- Backslash starts an escape sequence
          end_col = j
          in_escape = true
        elseif c:match("[A-Za-z0-9_./~ -]") then
          -- Regular path characters (including space when not escaped, but we allow it)
          end_col = j
        else
          break
        end
      end
      if not start_col then
        break
      end

      -- Check if this is actually a # pattern we already highlighted
      local matched_text = line:sub(start_col, end_col)
      if not matched_text:match("^#") then
        vim.api.nvim_buf_set_extmark(
          bufnr,
          highlight_ns,
          line_idx,
          start_col - 1,
          { end_col = end_col, hl_group = "AnyaFileRef", priority = 200 }
        )
      end
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
        vim.api.nvim_buf_set_extmark(
          bufnr,
          highlight_ns,
          line_idx,
          start_col - 1,
          { end_col = end_col, hl_group = "AnyaSlashCommand", priority = 200 }
        )
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
  callback = function()
    mark_prompt_typed()
    schedule_highlight()
  end,
  desc = "Schedule @filepath and /command reference highlighting",
})

vim.api.nvim_create_autocmd({ "BufEnter", "BufWinEnter" }, {
  buffer = bufnr,
  callback = function()
    if vim.api.nvim_buf_is_valid(bufnr) then
      highlight_refs()
    end
  end,
  desc = "Highlight references immediately on buffer enter",
})

-- Track leaving/entering prompt for navigation
vim.api.nvim_create_autocmd("WinLeave", {
  buffer = bufnr,
  callback = function()
    vim.g.anya_left_anya_win = true
    -- Save cursor position so we can restore it when coming back
    local cursor = vim.api.nvim_win_get_cursor(0)
    vim.g.anya_prompt_last_cursor = { cursor[1], cursor[2] }
    vim.g.anya_prompt_last_mode = (vim.api.nvim_get_mode().mode == "i") and "insert" or "normal"
  end,
  desc = "Track leaving Anya prompt window and save cursor",
})

vim.api.nvim_create_autocmd("WinEnter", {
  buffer = bufnr,
  callback = function()
    vim.g.anya_left_anya_win = false
    -- Restore cursor position and insert mode
    vim.schedule(function()
      if not vim.api.nvim_buf_is_valid(bufnr) then
        return
      end
      local saved_cursor = vim.g.anya_prompt_last_cursor
      if type(saved_cursor) == "table" and #saved_cursor >= 2 then
        local line_count = vim.api.nvim_buf_line_count(bufnr)
        local line = math.max(1, math.min(saved_cursor[1], line_count))
        local line_text = vim.api.nvim_buf_get_lines(bufnr, line - 1, line, false)[1] or ""
        local col = math.max(0, math.min(saved_cursor[2], #line_text))
        pcall(vim.api.nvim_win_set_cursor, 0, { line, col })
      end
      if vim.g.anya_prompt_last_mode == "insert" and vim.api.nvim_get_mode().mode ~= "i" then
        pcall(vim.cmd, "startinsert")
      elseif
        anya_config.start_in_insert
        and vim.g.anya_prompt_last_mode == nil
        and vim.api.nvim_get_mode().mode ~= "i"
      then
        pcall(vim.cmd, "startinsert")
      end
      highlight_refs()
    end)
  end,
  desc = "Track entering Anya prompt window and restore cursor",
})

-- Focus management: trap focus within Anya windows
-- This prevents accidentally navigating to the layout container window
vim.keymap.set("n", "<C-w>h", function()
  require("anya.float_focus").check_and_redirect()
end, { buffer = true, desc = "Navigate left (trapped)" })

-- Bare <C-h> also navigates left out of the pane
vim.keymap.set({ "n", "i" }, "<C-h>", function()
  require("anya.float_focus").focus_left()
end, { buffer = true, nowait = true, desc = "Navigate left out of Anya pane" })

vim.keymap.set("n", "<C-w>j", function()
  require("anya.float_focus").check_and_redirect()
end, { buffer = true, desc = "Navigate down (trapped)" })

vim.keymap.set("n", "<C-w>w", function()
  require("anya.float_focus").focus_chat()
end, { buffer = true, desc = "Cycle to chat window" })

vim.keymap.set("n", "<C-w><C-w>", function()
  require("anya.float_focus").focus_chat()
end, { buffer = true, desc = "Cycle to chat window" })



vim.keymap.set("n", "<C-w>k", function()
  require("anya.float_focus").focus_chat()
end, { buffer = true, desc = "Navigate to chat window" })

vim.keymap.set("n", "<C-w>l", function()
  require("anya.float_focus").check_and_redirect()
end, { buffer = true, desc = "Navigate right (trapped)" })

-- Also handle arrow variants
vim.keymap.set("n", "<C-w><Left>", function()
  require("anya.float_focus").check_and_redirect()
end, { buffer = true, desc = "Navigate left (trapped)" })

vim.keymap.set("n", "<C-w><Down>", function()
  require("anya.float_focus").check_and_redirect()
end, { buffer = true, desc = "Navigate down (trapped)" })

vim.keymap.set("n", "<C-w><Up>", function()
  require("anya.float_focus").focus_chat()
end, { buffer = true, desc = "Navigate to chat window" })

vim.keymap.set("n", "<C-w><Right>", function()
  require("anya.float_focus").check_and_redirect()
end, { buffer = true, desc = "Navigate right (trapped)" })

-- Movement keymaps
vim.keymap.set("n", "<C-a>", "0", { buffer = true, nowait = true, desc = "Start of line" })
vim.keymap.set("i", "<C-a>", "<C-o>0", { buffer = true, nowait = true, desc = "Start of line" })
vim.keymap.set("n", "<C-e>", "$", { buffer = true, nowait = true, desc = "End of line" })
vim.keymap.set("i", "<C-e>", "<C-o>$", { buffer = true, nowait = true, desc = "End of line" })
vim.keymap.set("n", "<C-u>", "S", { buffer = true, nowait = true, desc = "Delete whole line" })
vim.keymap.set("i", "<C-u>", "<C-o>S", { buffer = true, nowait = true, desc = "Delete whole line" })

-- Resize prompt height
vim.keymap.set(
  { "n", "i" },
  "<C-Up>",
  "<cmd>call AnyaResizePromptHeight(1)<cr>",
  { buffer = true, nowait = true, desc = "Increase prompt height" }
)
vim.keymap.set(
  { "n", "i" },
  "<C-Down>",
  "<cmd>call AnyaResizePromptHeight(-1)<cr>",
  { buffer = true, nowait = true, desc = "Decrease prompt height" }
)

-- Resize side pane width
local function resize_pane(delta)
  local win = vim.api.nvim_get_current_win()
  local config = vim.api.nvim_win_get_config(win)
  -- Check if we are in a floating window attached to a parent
  if config.relative == "win" and config.win then
    local parent_win = config.win
    -- Verify parent window is valid
    if vim.api.nvim_win_is_valid(parent_win) then
      vim.api.nvim_win_call(parent_win, function()
        vim.cmd("vertical resize " .. (delta > 0 and "+" or "") .. delta)
      end)
    end
  else
    -- Fallback for non-floating setup
    vim.cmd("vertical resize " .. (delta > 0 and "+" or "") .. delta)
  end
end

vim.keymap.set({ "n", "i" }, "<C-Left>", function()
  resize_pane(2)
end, { buffer = true, nowait = true, desc = "Shrink side pane" })
vim.keymap.set({ "n", "i" }, "<C-Right>", function()
  resize_pane(-2)
end, { buffer = true, nowait = true, desc = "Grow side pane" })

vim.keymap.set("n", "<localleader>t", function()
  require("anya.task_list").show_latest()
end, { buffer = true, desc = "Show latest task list" })

vim.keymap.set("n", "<localleader>p", function()
  require("anya.system_prompt").show()
end, { buffer = true, desc = "Open system prompt" })

vim.keymap.set("n", "<localleader>u", function()
  vim.cmd("UpdateRemotePlugins")
end, { buffer = true, desc = "Update remote plugins" })

vim.keymap.set("n", "<localleader>n", function()
  Snacks.notifier.show_history()
end, { buffer = true, desc = "Show latest task list" })

local function should_yield_tab_to_insert_plugins()
  if vim.fn.pumvisible() ~= 0 or vim.fn.wildmenumode() == 1 then
    return true
  end

  local copilot_vim_suggestion = vim.fn["copilot#GetDisplayedSuggestion"]
  if type(copilot_vim_suggestion) == "function" then
    local ok, suggestion = pcall(copilot_vim_suggestion)
    if ok and type(suggestion) == "table" and suggestion.text and suggestion.text ~= "" then
      return true
    end
  end

  local ok_copilot_lua, copilot_suggestion = pcall(require, "copilot.suggestion")
  if ok_copilot_lua and copilot_suggestion and type(copilot_suggestion.is_visible) == "function" then
    local ok_visible, visible = pcall(copilot_suggestion.is_visible)
    if ok_visible and visible then
      return true
    end
  end

  local ok_blink, blink = pcall(require, "blink.cmp")
  if ok_blink and blink then
    if type(blink.is_visible) == "function" then
      local ok_visible, visible = pcall(blink.is_visible)
      if ok_visible and visible then
        return true
      end
    end

    if type(blink.ghost_text_is_visible) == "function" then
      local ok_visible, visible = pcall(blink.ghost_text_is_visible)
      if ok_visible and visible then
        return true
      end
    end
  end

  return false
end

-- Focus toggle between chat and prompt with Tab (also set in buffers.py, but ftplugin ensures
-- it's always available and can handle insert mode)
vim.keymap.set("n", "<Tab>", function()
  require("anya.float_focus").focus_chat()
end, { buffer = true, nowait = true, desc = "Switch to chat window" })

vim.keymap.set("i", "<Tab>", function()
  if should_yield_tab_to_insert_plugins() then
    return "<Tab>"
  end

  vim.schedule(function()
    pcall(vim.cmd, "stopinsert")
    require("anya.float_focus").focus_chat()
  end)

  return ""
end, { buffer = true, nowait = true, expr = true, replace_keycodes = false, desc = "Switch to chat window" })

-- Focus chat window with Ctrl+k (also set in buffers.py, but ftplugin ensures it's always available)
vim.keymap.set("n", "<C-k>", function()
  require("anya.float_focus").focus_chat()
end, { buffer = true, nowait = true, desc = "Focus chat window" })

vim.keymap.set("i", "<C-k>", function()
  require("anya.float_focus").focus_chat()
end, { buffer = true, nowait = true, desc = "Focus chat window" })

local function schedule_render_safe_highlight()
  if highlight_scheduled then
    return
  end
  highlight_scheduled = true
  vim.schedule(function()
    highlight_scheduled = false
    if vim.api.nvim_buf_is_valid(bufnr) then
      highlight_refs()
    end
  end)
end

_G.anya_highlight_prompt_refs = schedule_render_safe_highlight
_G.anya_highlight_prompt_file_refs = schedule_render_safe_highlight

-- Initial highlight
vim.schedule(highlight_refs)
schedule_render_safe_highlight()

-- Trigger completions with @ for file mentions
-- This integrates with blink.cmp or other completion engines
-- The actual completion source is defined in lua/anya/blink/files.lua

-- Set up completion trigger characters
vim.opt_local.completeopt = "menu,menuone,noselect"

-- Prompt history navigation with <C-p> and <C-n>
local function cycle_history(direction)
  local history = require("anya.history")
  local current_bufnr = vim.api.nvim_get_current_buf()

  -- Get current buffer content
  local current_lines = vim.api.nvim_buf_get_lines(current_bufnr, 0, -1, false)
  local current_content = table.concat(current_lines, "\n")

  if not history.is_navigating() then
    -- Start navigation mode
    history.start_navigation(current_content)
  end

  -- Navigate in the specified direction
  local prompt
  if direction == "previous" then
    prompt = history.navigate_previous()
  elseif direction == "next" then
    prompt = history.navigate_next()
  end

  -- Update buffer if we got a prompt
  if prompt then
    local lines = vim.split(prompt, "\n", { plain = true })
    -- Set flag to prevent autocmds from stopping navigation
    _G._anya_set_navigating_programmatically(true)
    vim.api.nvim_buf_set_lines(current_bufnr, 0, -1, false, lines)
    -- Move cursor to end of buffer
    local last_line = #lines
    local last_col = #lines[last_line]
    vim.api.nvim_win_set_cursor(0, { last_line, last_col })
    -- Reset flag after a short delay to allow autocmds to fire and be ignored
    vim.defer_fn(function()
      _G._anya_set_navigating_programmatically(false)
    end, 10)
  end
end

-- Navigate to previous (older) prompt with <C-p>
vim.keymap.set("i", "<C-p>", function()
  -- Don't navigate if completion popup is visible to avoid disrupting completion
  if vim.fn.pumvisible() ~= 0 then
    return
  end

  cycle_history("previous")
end, { buffer = true, nowait = true, desc = "Previous prompt in history" })

vim.keymap.set("n", "<C-p>", function()
  cycle_history("previous")
end, { buffer = true, nowait = true, desc = "Previous prompt in history" })

-- Navigate to next (newer) prompt with <C-n>
vim.keymap.set("i", "<C-n>", function()
  -- Don't navigate if completion popup is visible to avoid disrupting completion
  if vim.fn.pumvisible() ~= 0 then
    return
  end

  cycle_history("next")
end, { buffer = true, nowait = true, desc = "Next prompt in history" })

vim.keymap.set("n", "<C-n>", function()
  cycle_history("next")
end, { buffer = true, nowait = true, desc = "Next prompt in history" })

local image_paste = require("anya.image_paste")
image_paste.setup()

-- Paste image with <C-v> in normal and insert mode
vim.keymap.set({ "n", "i" }, "<C-v>", function()
  image_paste.paste_from_clipboard(vim.fn.mode())
end, { buffer = true, desc = "Paste (image-aware)" })

vim.keymap.set("n", "<localleader>v", function()
  image_paste.paste_image(true)
end, { buffer = true, desc = "Paste image" })

-- Smart <CR> mapping for sending messages
-- In normal mode: Always send the message
-- In insert mode: Check if completion popup is visible first
-- If popup is visible, just insert newline (let blink.cmp handle completion)
-- If popup is not visible, exit insert mode and send message
vim.keymap.set("n", "<CR>", function()
  require("anya.conversation").send_message()
end, { buffer = true, desc = "Send message" })

vim.keymap.set("i", "<CR>", function()
  -- Check if completion popup menu is visible
  if vim.fn.pumvisible() ~= 0 then
    -- Popup is open, just return CR to confirm completion
    return "<CR>"
  else
    -- No popup, send message while staying in insert mode
    vim.schedule(function()
      require("anya.conversation").send_message()
    end)
    return ""
  end
end, { buffer = true, expr = true, desc = "Send message or confirm completion" })
