-- Foldtext implementation for anya-chat buffers
-- Handles concealed markers properly in fold text

local M = {}

-- Strip HTML comment markers from text
local function strip_markers(text)
  return text:gsub("^<!%-%- anya__[^>]*%-%->", ""):gsub("^<!%-%- ", ""):gsub(" %-%->$", "")
end

-- Get custom fold text for a fold
function M.get_foldtext()
  local start_line = vim.v.foldstart
  local end_line = vim.v.foldend

  -- Get the line content
  local line = vim.api.nvim_buf_get_lines(0, start_line - 1, start_line, false)[1] or ""

  -- Strip markers from the display text
  local display_line = strip_markers(line)

  -- Count lines in fold (excluding marker lines)
  local line_count = 0
  for i = start_line, end_line do
    local check_line = vim.api.nvim_buf_get_lines(0, i - 1, i, false)[1] or ""
    if not check_line:match("^<!%-%- anya__") then
      line_count = line_count + 1
    end
  end

  -- Build fold text
  local prefix = "├─ "
  local suffix = string.format(" (%d lines)", line_count)
  local available_width = vim.v.columns - vim.fn.strdisplaywidth(prefix) - vim.fn.strdisplaywidth(suffix) - 1

  -- Truncate line if too long
  if vim.fn.strdisplaywidth(display_line) > available_width then
    display_line = vim.fn.strcharpart(display_line, 0, available_width) .. "…"
  end

  return prefix .. display_line .. suffix
end

return M
