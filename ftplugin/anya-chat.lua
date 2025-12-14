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

-- Track last Anya window for navigation redirection
local last_anya_win = nil

vim.api.nvim_create_autocmd("WinLeave", {
  buffer = bufnr,
  callback = function()
    vim.g.anya_left_anya_win = true
    last_anya_win = vim.api.nvim_get_current_win()
  end,
  desc = "Track leaving Anya chat window",
})

vim.api.nvim_create_autocmd("WinEnter", {
  buffer = bufnr,
  callback = function()
    last_anya_win = vim.api.nvim_get_current_win()
    vim.g.anya_left_anya_win = false

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

-- Navigate from chat to prompt float
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
  require("anya.float_focus").focus_prompt()
end, { buffer = true, desc = "Focus chat window (toggle)" })

-- Toggle YOLO mode
vim.keymap.set("n", "<localleader>y", function()
  require("anya.conversation").toggle_yolo_mode()
end, { buffer = true, desc = "Toggle YOLO mode" })

-- Expose globally so streaming can call it
_G.anya_highlight_chat_file_refs = highlight_refs

highlight_refs()
