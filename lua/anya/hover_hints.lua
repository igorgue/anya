-- Hover hints for tool calls in Anya chat buffer
-- Shows virtual text hints when hovering over tool call headers

local M = {}
local markers = require("anya.markers")

-- Namespace for hover hints
M.ns_id = vim.api.nvim_create_namespace("anya_hover_hints")

-- Track current hover extmark for cleanup
M._current_hover = nil -- { bufnr, extmark_id }
M._timers = M._timers or {} -- { [bufnr] = luv_timer }

-- Check if a line is a tool call header (has [[title]])
-- @param line string The line to check
-- @return boolean, string|nil True if tool line, and optional type ("code")
local function is_tool_header_line(line)
  if line:match("%[%[.-%]%]") then
    return true, "code"
  end

  return false, nil
end

local function get_title_at_line(line)
  local title = line:match("%[%[(.-)%]%]")
  if not title or title == "" then
    return nil
  end
  return title
end

local function title_has_saved_file(kind, title)
  if not title or title == "" then
    return false
  end

  local cwd = vim.fn.getcwd()
  local sanitized = title:lower()
  sanitized = (sanitized:match("^%s*(.-)%s*$") or "")
  sanitized = sanitized:gsub("[^a-z0-9]+", "-")
  sanitized = (sanitized:match("^%-*(.-)%-*$") or "")
  if sanitized == "" then
    sanitized = "untitled"
  end

  local ext = kind == "code" and "py" or "txt"
  local pattern = string.format("%s/.anya/%s/%s-*.%s", cwd, kind, sanitized, ext)
  local matches = vim.fn.glob(pattern, false, true)
  return matches and #matches > 0
end

local function stop_timer(bufnr)
  local timer = M._timers[bufnr]
  if timer then
    timer:stop()
    timer:close()
    M._timers[bufnr] = nil
  end
end

-- Check if current cursor line is within a tool fold (has fold_start marker above)
-- @param bufnr number Buffer number
-- @param line_num number Current line number (1-indexed)
-- @return boolean True if cursor is on or near a tool fold header
local function is_near_tool_fold(bufnr, line_num)
  local lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)

  -- Check a few lines above for fold_start marker
  for i = math.max(1, line_num - 3), line_num do
    local line = lines[i]
    if line and markers.is_marker_line(line) then
      local found_markers = markers.parse_marker(line)
      if found_markers then
        for _, marker_name in ipairs(found_markers) do
          if marker_name == markers.fold_start then
            return true
          end
        end
      end
    end
  end

  return false
end

-- Show hover hint on current line
-- @param bufnr number Buffer number
-- @param line_num number Line number (1-indexed)
local function show_hover_hint(bufnr, line_num)
  M.clear_hover_hint()

  if not vim.api.nvim_buf_is_valid(bufnr) then
    return
  end

  local current_win = vim.api.nvim_get_current_win()
  if vim.api.nvim_win_get_buf(current_win) ~= bufnr then
    return
  end

  local cursor = vim.api.nvim_win_get_cursor(current_win)
  if cursor[1] ~= line_num then
    return
  end

  local lines = vim.api.nvim_buf_get_lines(bufnr, line_num - 1, line_num, false)
  if #lines == 0 then
    return
  end

  local line = lines[1]
  local is_tool, _tool_type = is_tool_header_line(line)
  local near_fold = is_near_tool_fold(bufnr, line_num)

  if not is_tool and not near_fold then
    return
  end

  local hint_parts = {}
  local title = get_title_at_line(line)
  local has_code = title_has_saved_file("code", title)
  local has_output = title_has_saved_file("output", title)

  if title and (has_code or has_output) then
    table.insert(hint_parts, { "Press ", "Comment" })
    if has_code then
      table.insert(hint_parts, { "Enter", "AnyaEditPending" })
      table.insert(hint_parts, { " for code", "Comment" })
    end
    if has_output then
      if has_code then
        table.insert(hint_parts, { " | ", "Comment" })
      end
      table.insert(hint_parts, { "go", "AnyaEditPending" })
      table.insert(hint_parts, { " for output", "Comment" })
    end
  else
    return
  end

  local line_idx = line_num - 1
  local extmark_id = vim.api.nvim_buf_set_extmark(bufnr, M.ns_id, line_idx, 0, {
    virt_text = hint_parts,
    virt_text_pos = "right_align",
    hl_mode = "combine",
    virt_text_hide = false,
  })

  M._current_hover = { bufnr = bufnr, extmark_id = extmark_id }
end

local function schedule_hover_hint(bufnr)
  stop_timer(bufnr)

  if not vim.api.nvim_buf_is_valid(bufnr) then
    return
  end

  local timer = vim.uv.new_timer()
  M._timers[bufnr] = timer
  timer:start(
    150,
    0,
    vim.schedule_wrap(function()
      stop_timer(bufnr)
      if not vim.api.nvim_buf_is_valid(bufnr) then
        return
      end
      local current_win = vim.api.nvim_get_current_win()
      if vim.api.nvim_win_get_buf(current_win) ~= bufnr then
        return
      end
      local cursor = vim.api.nvim_win_get_cursor(current_win)
      show_hover_hint(bufnr, cursor[1])
    end)
  )
end

function M.clear_hover_hint()
  if M._current_hover then
    local bufnr = M._current_hover.bufnr
    local extmark_id = M._current_hover.extmark_id

    if vim.api.nvim_buf_is_valid(bufnr) then
      pcall(vim.api.nvim_buf_del_extmark, bufnr, M.ns_id, extmark_id)
    end

    M._current_hover = nil
  end
end

-- Setup hover hints for a buffer
-- @param bufnr number Buffer number
function M.setup(bufnr)
  vim.api.nvim_buf_clear_namespace(bufnr, M.ns_id, 0, -1)
  stop_timer(bufnr)

  vim.api.nvim_create_autocmd({ "CursorHold", "CursorHoldI" }, {
    buffer = bufnr,
    callback = function()
      local cursor = vim.api.nvim_win_get_cursor(0)
      show_hover_hint(bufnr, cursor[1])
    end,
    desc = "Show hover hints for tool calls",
  })

  vim.api.nvim_create_autocmd({ "CursorMoved", "CursorMovedI" }, {
    buffer = bufnr,
    callback = function()
      M.clear_hover_hint()
      schedule_hover_hint(bufnr)
    end,
    desc = "Refresh hover hints when cursor moves",
  })

  vim.api.nvim_create_autocmd("BufLeave", {
    buffer = bufnr,
    callback = function()
      stop_timer(bufnr)
      M.clear_hover_hint()
    end,
    desc = "Clear hover hints when leaving buffer",
  })

  vim.api.nvim_create_autocmd("BufWipeout", {
    buffer = bufnr,
    callback = function()
      stop_timer(bufnr)
      M.clear_hover_hint()
    end,
    desc = "Clean up hover hint timer",
  })
end

return M
