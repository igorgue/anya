-- Cursor positioning utilities for agent.nvim
local M = {}

--- Keep cursor centered in the prompt buffer when in insert mode
-- Avoids going under the floating toolbar at the bottom
function M.keep_centered()
  local win = vim.api.nvim_get_current_win()
  local buf = vim.api.nvim_win_get_buf(win)
  
  -- Only apply to agent-prompt buffers
  if vim.bo[buf].filetype ~= "agent-prompt" then
    return
  end
  
  -- Only apply in insert mode
  local mode = vim.api.nvim_get_mode().mode
  if not mode:match("i") then
    return
  end
  
  -- Get window and content dimensions
  local height = vim.api.nvim_win_get_height(win)
  local line_count = vim.api.nvim_buf_line_count(buf)
  
  -- Reserve space for toolbar (2 lines at bottom)
  local toolbar_reserve = 2
  local effective_height = height - toolbar_reserve
  
  -- Only apply if there's enough content to scroll
  if line_count <= effective_height then
    return
  end
  
  -- Target position: middle of effective area (not full window)
  local target_line = math.floor(effective_height / 2)
  
  -- Get current cursor line (1-based)
  local cursor_line = vim.api.nvim_win_get_cursor(win)[1]
  
  -- Only scroll if cursor is significantly away from target (more than 1 line)
  if math.abs(cursor_line - target_line) > 1 then
    vim.api.nvim_win_set_cursor(win, {target_line, vim.api.nvim_win_get_cursor(win)[2]})
  end
end

return M
