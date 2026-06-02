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
-- Prevent "save file?" prompts - buffer is managed by Anya
vim.bo.modified = false
-- Clear winbar initially to prevent navic/other plugins from interfering
-- Our winbar will be set from Python after all windows are created (see buffers.py)
vim.opt.winbar = ""
vim.opt.showbreak = " "

-- Custom foldtext that handles concealed markers
vim.opt_local.foldtext = [[v:lua.require'anya.foldtext'.get_foldtext()]]

-- Cancel agent response with Ctrl+C
vim.keymap.set("n", "<C-c>", function()
  vim.cmd("Anya cancel")
end, { buffer = true, desc = "Cancel agent response" })

-- Tool output viewing keymaps
local tool_output = require("anya.tool_output")

-- Hover hints for tool calls
local hover_hints = require("anya.hover_hints")
hover_hints.setup(vim.api.nvim_get_current_buf())

-- Open code on <CR>, else toggle fold
vim.keymap.set("n", "<CR>", function()
  if not tool_output.open_code_at_cursor() then
    -- Try to toggle fold, ignore error if no fold exists
    pcall(vim.cmd, "normal! za")
  end
end, { buffer = true, desc = "Open code or toggle fold" })

-- Open tool output on `go`
vim.keymap.set("n", "go", function()
  tool_output.open_output_at_cursor()
end, { buffer = true, desc = "Open tool output for code at cursor" })

-- Right-click to open tool output (suppress default popup menu for this buffer)
vim.keymap.set("n", "<RightMouse>", "<Nop>", { buffer = true })
vim.keymap.set("n", "<RightRelease>", function()
  local mpos = vim.fn.getmousepos()
  vim.schedule(function()
    tool_output.open_output_at_cursor(mpos.line, mpos.column)
  end)
end, { buffer = true, desc = "Open tool output on right-click" })

-- Single-click to open code (mouse support)
-- Capture mouse position immediately (before vim.schedule) for accurate column detection.
vim.keymap.set("n", "<LeftRelease>", function()
  local mpos = vim.fn.getmousepos()
  vim.schedule(function()
    tool_output.open_code_at_cursor(mpos.line, mpos.column)
  end)
end, { buffer = true, desc = "Open code on click" })

-- Section navigation: jump between # headers (# User, # Anya, etc.)
local function jump_to_header(direction)
  local pattern = "^# "
  local flags = direction == "next" and "W" or "bW"
  local found = vim.fn.search(pattern, flags)
  if found == 0 then
    -- Wrap around if not found
    local wrap_flags = direction == "next" and "w" or "bw"
    vim.fn.search(pattern, wrap_flags)
  end
end

vim.keymap.set("n", "]]", function()
  jump_to_header("next")
end, { buffer = true, desc = "Jump to next header" })

vim.keymap.set("n", "[[", function()
  jump_to_header("prev")
end, { buffer = true, desc = "Jump to previous header" })

-- Edit approval keymaps are set up by edit_view.setup_keymaps() when edit blocks are rendered
-- This ensures the 1/2 keys only work within edit block extmark ranges

-- Highlight @filepath, #conv_id, and /commands using extmarks (works with treesitter)
vim.api.nvim_set_hl(0, "AnyaFileRef", { link = "Constant", default = true })
vim.api.nvim_set_hl(0, "AnyaConvRef", { link = "Function", default = true })
vim.api.nvim_set_hl(0, "AnyaSlashCommand", { link = "Special", default = true })

local highlight_ns = vim.api.nvim_create_namespace("anya_chat_highlights")
local bufnr = vim.api.nvim_get_current_buf()

local highlight_timer = nil
local highlight_pending = false
local highlight_scheduled = false

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

    -- Find all @filepath references first
    while true do
      -- Use a custom scanner to handle \-escaped spaces in file paths
      local start_col = line:find("@", pos)
      if not start_col then break end
      local end_col = start_col
      local in_escape = false
      for j = start_col + 1, #line do
        local c = line:sub(j, j)
        if in_escape then
          end_col = j
          in_escape = false
        elseif c == "\\" then
          end_col = j
          in_escape = true
        elseif c:match("[A-Za-z0-9_./~ -]") then
          end_col = j
        else
          break
        end
      end
      if not start_col then
        break
      end

      vim.api.nvim_buf_set_extmark(
        bufnr,
        highlight_ns,
        line_idx,
        start_col - 1,
        { end_col = end_col, hl_group = "AnyaFileRef", priority = 200 }
      )
      pos = end_col + 1
    end

    -- Find all #conv_id references
    pos = 1
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

-- For BufEnter/BufWinEnter, highlight immediately (no debounce)
local function highlight_immediate()
  if highlight_timer then
    highlight_timer:stop()
    highlight_timer = nil
  end
  highlight_pending = false
  highlight_refs()
end

vim.api.nvim_create_autocmd({ "TextChanged", "TextChangedI" }, {
  buffer = bufnr,
  callback = schedule_highlight,
  desc = "Schedule @filepath and /command reference highlighting",
})

vim.api.nvim_create_autocmd({ "BufEnter", "BufWinEnter" }, {
  buffer = bufnr,
  callback = highlight_immediate,
  desc = "Highlight @filepath and /command references immediately on buffer enter",
})

vim.api.nvim_create_autocmd("WinLeave", {
  buffer = bufnr,
  callback = function()
    vim.g.anya_left_anya_win = true
  end,
  desc = "Track leaving Anya chat window",
})

vim.api.nvim_create_autocmd("WinEnter", {
  buffer = bufnr,
  callback = function()
    vim.g.anya_left_anya_win = false

    -- Re-apply our highlights after render-markdown finishes re-rendering
    vim.schedule(function()
      if vim.api.nvim_buf_is_valid(bufnr) then
        highlight_refs()
      end
    end)

    -- Refresh winbar highlight to ensure it's properly styled
    local win = vim.api.nvim_get_current_win()
    if win > 0 and vim.api.nvim_win_is_valid(win) then
      vim.api.nvim_win_set_option(
        win,
        "winhighlight",
        "Normal:Normal,NormalFloat:Normal,WinBar:AnyaWinBar,WinBarNC:AnyaWinBar"
      )
    end

    -- If we entered from outside Anya and user pressed <C-w>j,
    -- redirect to prompt float
    local prev_win = vim.fn.win_getid(vim.fn.winnr("#"))
    if prev_win ~= 0 and vim.api.nvim_win_is_valid(prev_win) then
      local prev_buf = vim.api.nvim_win_get_buf(prev_win)
      local prev_ft = vim.api.nvim_buf_get_option(prev_buf, "filetype")
      -- If previous window was not Anya, don't redirect
      if not prev_ft:match("^anya%-") then
        return
      end
    end
  end,
  desc = "Track entering Anya chat window and refresh winbar highlight",
})

-- Make window cycling toggle directly between chat and prompt.
-- Native <C-w>w hits the hidden layout container first, so we intercept it here.
vim.keymap.set("n", "<C-w>w", function()
  require("anya.float_focus").focus_prompt()
end, { buffer = true, desc = "Cycle to prompt window" })

vim.keymap.set("n", "<C-w><C-w>", function()
  require("anya.float_focus").focus_prompt()
end, { buffer = true, desc = "Cycle to prompt window" })

vim.keymap.set("n", "<C-w>j", function()
  require("anya.float_focus").focus_prompt()
end, { buffer = true, desc = "Focus prompt window" })

vim.keymap.set("n", "<C-w><C-j>", function()
  require("anya.float_focus").focus_prompt()
end, { buffer = true, desc = "Focus prompt window" })

-- Also map bare <C-j> for users who have that mapped
vim.keymap.set("n", "<C-j>", function()
  require("anya.float_focus").focus_prompt()
end, { buffer = true, desc = "Focus prompt window" })

vim.keymap.set("n", "<localleader><localleader>", function()
  require("anya.float_focus").toggle_focus()
end, { buffer = true, desc = "Toggle between chat and prompt" })

-- toggle history
vim.keymap.set("n", "<localleader>h", function()
  vim.cmd("Anya history")
end, { buffer = true, desc = "Open history" })

-- Restore last session
vim.keymap.set("n", "<localleader>l", function()
  require("anya.picker").restore_last_session()
end, { buffer = true, desc = "Restore last session" })

vim.keymap.set("n", "<localleader>T", function()
  require("anya.telegram").reopen_pairing()
end, { buffer = true, desc = "Reopen Telegram pairing info" })


-- Daemon management keymaps
vim.keymap.set("n", "<localleader>s", function()
  vim.cmd("Anya daemon start")
end, { buffer = true, desc = "Start Anya daemon" })

vim.keymap.set("n", "<localleader>S", function()
  vim.cmd("Anya daemon stop")
end, { buffer = true, desc = "Stop Anya daemon" })

vim.keymap.set("n", "<localleader>r", function()
  vim.cmd("Anya daemon restart")
end, { buffer = true, desc = "Restart Anya daemon" })

vim.keymap.set("n", "<localleader>t", function()
  require("anya.task_list").show_latest()
end, { buffer = true, desc = "Show latest task list" })

-- Open system prompt
vim.keymap.set("n", "<localleader>p", function()
  require("anya.system_prompt").show()
end, { buffer = true, desc = "Open system prompt" })

-- <C-k> in chat does nothing special (stay in chat, no window above)
-- But we map it to prevent accidental navigation out of Anya
vim.keymap.set("n", "<C-k>", function()
  -- No-op: already at top window
end, { buffer = true, nowait = true, desc = "Stay in chat (top window)" })

-- <C-h> navigates left out of the Anya pane (pane layout) or does nothing
vim.keymap.set("n", "<C-h>", function()
  require("anya.float_focus").focus_left()
end, { buffer = true, nowait = true, desc = "Navigate left out of Anya pane" })

-- Focus toggle between chat and prompt with Tab
vim.keymap.set("n", "<Tab>", function()
  require("anya.float_focus").focus_prompt()
end, { buffer = true, nowait = true, desc = "Switch to prompt window" })

vim.keymap.set("i", "<Tab>", function()
  if vim.fn.pumvisible() ~= 0 or vim.fn.wildmenumode() == 1 then
    return "<Tab>"
  end

  vim.schedule(function()
    pcall(vim.cmd, "stopinsert")
    require("anya.float_focus").focus_prompt()
  end)

  return ""
end, { buffer = true, nowait = true, expr = true, replace_keycodes = false, desc = "Switch to prompt window" })

-- Resize prompt height from chat window too
local function resize_prompt_height(delta)
  vim.fn.AnyaResizePromptHeight(delta)
end

vim.keymap.set("n", "<C-Up>", function()
  resize_prompt_height(1)
end, { buffer = true, nowait = true, desc = "Grow prompt height" })
vim.keymap.set("n", "<C-Down>", function()
  resize_prompt_height(-1)
end, { buffer = true, nowait = true, desc = "Reduce prompt height" })

-- Terminal escape sequence variants for Ctrl+Up/Down
-- vim.keymap.set("n", "<Esc>[1;5A", function()
--   resize_prompt_height(1)
-- end, { buffer = true, desc = "Grow prompt height (CSI)" })
-- vim.keymap.set("n", "<Esc>[1;5B", function()
--   resize_prompt_height(-1)
-- end, { buffer = true, desc = "Reduce prompt height (CSI)" })
-- vim.keymap.set("n", "<Esc>Oa", function()
--   resize_prompt_height(1)
-- end, { buffer = true, desc = "Grow prompt height (Alt CSI)" })
-- vim.keymap.set("n", "<Esc>Ob", function()
--   resize_prompt_height(-1)
-- end, { buffer = true, desc = "Reduce prompt height (Alt CSI)" })

vim.keymap.set({ "n" }, "<localleader>t", function()
  require("anya.task_list").show_latest()
end, { buffer = true, desc = "Show latest task list" })

vim.keymap.set("n", "<localleader>p", function()
  require("anya.system_prompt").show()
end, { buffer = true, desc = "Open system prompt" })

vim.keymap.set("n", "<localleader>u", function()
  vim.cmd("UpdateRemotePlugins")
end, { buffer = true, desc = "Update remote plugins" })

vim.keymap.set({ "n" }, "<localleader>n", function()
  Snacks.notifier.show_history()
end, { buffer = true, desc = "Show latest task list" })

-- Register which-key group for localleader keymaps (forces re-scan after ftplugin sets them)
local wk_ok, wk = pcall(require, "which-key")
if wk_ok then
  wk.add({
    { "<localleader>", group = "Anya", buffer = vim.api.nvim_get_current_buf() },
  })
end

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

vim.api.nvim_create_autocmd({ "TextChanged", "TextChangedI" }, {
  buffer = bufnr,
  callback = schedule_render_safe_highlight,
  desc = "Re-apply reference highlights after render-markdown updates",
})

_G.anya_highlight_chat_file_refs = schedule_render_safe_highlight
_G.anya_highlight_chat_refs = schedule_render_safe_highlight

highlight_refs()
schedule_render_safe_highlight()
