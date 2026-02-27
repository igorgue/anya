-- UI Utilities for Anya plugin
-- Handles colors, icons, and highlight setup

local M = {}

M.icons = {
  pending = "", -- Circle for pending
  success = "", -- Checkmark for success
  failure = "", -- Cross for failure
  thinking = "󰧑", -- Thinking brain for thinking reasoning text
  tool_output = "󰈙", -- File icon for tool output reference
}

-- Namespace for extmarks
M.ns_id = vim.api.nvim_create_namespace("anya_markers")
M.edit_view_ns_id = vim.api.nvim_create_namespace("anya_edit_view")

-- Edit block highlight groups (imported from edit_view)
M.HL_SEARCH = "AnyaEditSearch"
M.HL_REPLACE = "AnyaEditReplace"
M.HL_MARKER = "AnyaEditMarker"
M.HL_DIVIDER = "AnyaEditDivider"

-- Helper to create highlight with fg from source but transparent bg for combining
function M.set_hl_fg_only(name, source, extra)
  local hl = vim.api.nvim_get_hl(0, { name = source, link = false })
  local opts = {
    fg = hl.fg,
    bg = "NONE",
    sp = hl.sp,
    blend = 0, -- Fully opaque fg, transparent bg for hl_mode="combine"
  }
  if extra then
    for k, v in pairs(extra) do
      opts[k] = v
    end
  end
  vim.api.nvim_set_hl(0, name, opts)
end

-- Setup highlight groups (fg inherited, bg transparent)
function M.setup_highlights()
  -- Success: green (from OkMsg)
  M.set_hl_fg_only("AnyaToolSuccess", "OkMsg")

  -- Failure: red (from ErrorMsg)
  M.set_hl_fg_only("AnyaToolFailure", "ErrorMsg")

  -- Pending: subtle (from Comment)
  M.set_hl_fg_only("AnyaToolPending", "Comment")

  -- Thinking: gray for reasoning text (from Comment)
  M.set_hl_fg_only("AnyaThinking", "Comment")

  -- Token bar highlights
  M.set_hl_fg_only("AnyaTokenGreen", "DiagnosticOk")
  M.set_hl_fg_only("AnyaTokenYellow", "WarningMsg")
  M.set_hl_fg_only("AnyaTokenRed", "ErrorMsg")
  M.set_hl_fg_only("AnyaTokenGray", "Comment")

  -- Edit tool highlight groups
  -- Diff indicators
  M.set_hl_fg_only("AnyaEditAdd", "OkMsg")
  M.set_hl_fg_only("AnyaEditChange", "WarningMsg")
  M.set_hl_fg_only("AnyaEditDelete", "ErrorMsg")

  -- Filename (from Constant)
  M.set_hl_fg_only("AnyaEditFilename", "Constant")

  -- Widget text (from Normal)
  M.set_hl_fg_only("AnyaEditWidget", "Normal")

  -- Widget text bold variant (for selected action)
  M.set_hl_fg_only("AnyaEditWidgetBold", "Normal", { bold = true })

  -- Edit block content highlights (for SEARCH/REPLACE sections)
  local diff_del = vim.api.nvim_get_hl(0, { name = "DiffDelete", link = false })
  local diff_add = vim.api.nvim_get_hl(0, { name = "DiffAdd", link = false })
  local comment_hl = vim.api.nvim_get_hl(0, { name = "Comment", link = false })
  vim.api.nvim_set_hl(0, M.HL_SEARCH, { bg = diff_del.bg, fg = diff_del.fg })
  vim.api.nvim_set_hl(0, M.HL_REPLACE, { bg = diff_add.bg, fg = diff_add.fg })
  vim.api.nvim_set_hl(0, M.HL_MARKER, { fg = comment_hl.fg, bold = true })
  vim.api.nvim_set_hl(0, M.HL_DIVIDER, { fg = comment_hl.fg })

  -- Edit widget control highlights (accept/reject buttons)
  local ok_hl = vim.api.nvim_get_hl(0, { name = "DiagnosticOk", link = false })
  if not ok_hl.fg then
    ok_hl = vim.api.nvim_get_hl(0, { name = "String", link = false })
  end
  local err_hl = vim.api.nvim_get_hl(0, { name = "ErrorMsg", link = false })
  vim.api.nvim_set_hl(0, "AnyaEditAccept", { fg = ok_hl.fg })
  vim.api.nvim_set_hl(0, "AnyaEditReject", { fg = err_hl.fg })
  vim.api.nvim_set_hl(0, "AnyaEditPending", { fg = comment_hl.fg })

  -- File reference highlight (@filepath)
  M.set_hl_fg_only("AnyaFileRef", "Constant", { underline = true })

  -- Winbar highlight group (links to Comment)
  vim.api.nvim_set_hl(0, "AnyaWinBar", { link = "Comment" })
end


-- Handle click on token progress bar
-- @param id: Click region ID (unused)
-- @param clicks: Number of clicks (unused)
-- @param button: Mouse button (unused)
-- @param mods: Modifier keys (unused)
function M.handle_token_click(id, clicks, button, mods)
  -- Toggle between compact and detailed views
  if M._token_view_state == "compact" then
    M._token_view_state = "detailed"
  else
    M._token_view_state = "compact"
  end
  -- Refresh winbar in all anya-chat windows by resetting the expression
  -- This ensures the winbar stays dynamic and both toggles work independently
  for _, win in ipairs(vim.api.nvim_list_wins()) do
    local buf = vim.api.nvim_win_get_buf(win)
    local ft = vim.api.nvim_get_option_value("filetype", { buf = buf })
    if ft == "anya-chat" then
      -- Reset to empty first, then restore the expression to force re-evaluation
      pcall(vim.api.nvim_win_set_option, win, "winbar", "")
      pcall(vim.api.nvim_win_set_option, win, "winbar", "%{%v:lua.require('anya.ui_utils').get_winbar()%}")
    end
  end
end

-- Global wrapper for winbar click handlers
-- These must be global for v:lua to work in winbar expressions

_G.anya_handle_token_click = function(id, clicks, button, mods)
  M.handle_token_click(id, clicks, button, mods)
end

-- Token stats storage (updated by daemon via set_token_stats)
M._token_stats = {
  used = 0,
  max = 128000,
  percentage = 0,
}

-- Token view state: "compact" or "detailed"
M._token_view_state = "compact"

-- Set token stats from Python plugin
-- @param used: Total tokens used
-- @param max: Context window size
-- @param percentage: Pre-calculated percentage
function M.set_token_stats(used, max, percentage)
  M._token_stats.used = used or 0
  M._token_stats.max = max or 128000
  M._token_stats.percentage = percentage or 0
  -- Refresh winbar in all anya-chat windows by resetting the expression
  for _, win in ipairs(vim.api.nvim_list_wins()) do
    local buf = vim.api.nvim_win_get_buf(win)
    local ft = vim.api.nvim_get_option_value("filetype", { buf = buf })
    if ft == "anya-chat" then
      -- Reset to empty first, then restore the expression to force re-evaluation
      pcall(vim.api.nvim_win_set_option, win, "winbar", "")
      pcall(vim.api.nvim_win_set_option, win, "winbar", "%{%v:lua.require('anya.ui_utils').get_winbar()%}")
    end
  end
end

-- Helper for getting token usage from stored stats
local function get_token_usage()
  return M._token_stats.used, M._token_stats.max, M._token_stats.percentage
end

-- Format a number (e.g., 128000 -> "128K")
local function format_number(num)
  if num >= 1000000 then
    return string.format("%.1fM", num / 1000000)
  elseif num >= 1000 then
    return string.format("%.0fK", num / 1000)
  else
    return tostring(num)
  end
end

-- Colored progress bar generator: xx% ▬▬▬ (compact) or detailed (used/max)
-- Note: We show the actual percentage even if over 100% - let the LLM handle overflow
function M.token_progress_bar()
  local used, max, percentage = get_token_usage()
  -- Don't show bar if no tokens used yet
  if not used or used == 0 then
    return ""
  end
  -- Use pre-calculated percentage, fallback to computing it
  local pct = percentage or (max > 0 and math.floor((used / max) * 100) or 0)
  if pct < 0 then
    pct = 0
  end
  -- NOTE: We intentionally do NOT cap at 100% here
  -- Let the actual percentage show, even if over 100%
  -- The LLM will handle any context overflow - we just display the info

  -- Color based on percentage (for bar visualization, cap at 100%)
  local display_pct = math.min(pct, 100)
  local color_group
  if pct <= 50 then
    color_group = "AnyaTokenGreen"
  elseif pct <= 80 then
    color_group = "AnyaTokenYellow"
  else
    color_group = "AnyaTokenRed"
  end

  -- Return based on view state
  if M._token_view_state == "compact" then
    -- Compact view: xx% ▬▬▬
    -- Bar visual is capped at 100% but percentage shows actual value
    local filled = math.floor(display_pct / 100 * 3 + 0.5)
    if filled < 1 and pct > 0 then
      filled = 1
    end -- Show at least 1 if any usage
    local unfilled = 3 - filled
    local bar = string.format(
      "%%#%s#%s%%*%%#AnyaTokenGray#%s%%*",
      color_group,
      string.rep("▬", filled),
      string.rep("▬", unfilled)
    )
    -- Show actual percentage (may be > 100%)
    return string.format("%3d%%%% %s", pct, bar)
  else
    -- Detailed view: used/max
    local used_str = format_number(used)
    local max_str = format_number(max)
    return string.format("%%#%s#%s*/%s%%*", color_group, used_str, max_str)
  end
end

function M.get_winbar()
  -- Safely get version
  local ok, version = pcall(vim.fn.AnyaVersion)
  local version_text = "Anya"
  if ok and version then
    version_text = "Anya v" .. version
  end

  -- Token progress bar (clickable)
  local token_bar = M.token_progress_bar()
  local token_click = ""
  if token_bar ~= "" then
    token_click = "%@v:lua.anya_handle_token_click@" .. token_bar .. "%T"
  end

  return string.format("%s%%=%s", version_text, token_click)
end

return M
