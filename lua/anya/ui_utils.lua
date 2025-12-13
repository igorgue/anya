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
end

return M
