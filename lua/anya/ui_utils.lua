-- UI Utilities for Anya plugin
-- Handles colors, icons, and highlight setup

local M = {}

M.icons = {
  pending = "", -- Circle for pending
  success = "", -- Checkmark for success
  failure = "", -- Cross for failure
  thinking = "󰧑", -- Thinking brain for thinking reasoning text
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

-- Handle click on YOLO section in winbar
function M.handle_yolo_click()
  require("anya.conversation").toggle_yolo_mode()
  -- Winbar expressions are re-evaluated automatically when the window is redrawn
  -- Schedule a redraw to update the winbar display
  vim.schedule(function()
    vim.cmd("redrawstatus")
  end)
end

-- Get winbar text for chat buffer
function M.get_winbar()
  -- Safely get version
  local ok, version = pcall(vim.fn.AnyaVersion)
  local version_text = "Anya"
  if ok and version then
    version_text = "Anya v" .. version
  end

  -- Try to safely get YOLO mode status - only if we're in a safe context
  -- Check if we're in a callback-safe context by checking mode
  local is_yolo_on = false
  local mode = vim.api.nvim_get_mode().mode
  -- Only try to get YOLO mode if we're not in a restricted mode
  if mode ~= "" then
    local yolo_ok, yolo_mode = pcall(vim.fn.AnyaGetYoloMode)
    if yolo_ok and type(yolo_mode) == "boolean" then
      is_yolo_on = yolo_mode
    end
  end

  -- Build winbar: left side (version), right side (YOLO status)
  -- %= pushes content to the right
  -- %@function@text%T makes text clickable
  -- %#Group#text%* applies highlight group
  local yolo_text
  if is_yolo_on then
    -- When ON: "YOLO: " (default) + "on" (OkMsg highlight)
    yolo_text = "YOLO: %#OkMsg#on%*"
  else
    -- When OFF: "YOLO: " + "off" (both use AnyaWinBar/default)
    yolo_text = "YOLO: off"
  end

  -- Make the entire YOLO section clickable
  -- Format: left_text %= %@click_handler@clickable_text%T
  return version_text .. "%=%@v:lua.require('anya.ui_utils').handle_yolo_click@" .. yolo_text .. "%T"
end

return M
