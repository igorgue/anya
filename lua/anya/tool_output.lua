-- Tool output viewing utilities for Anya plugin
-- Opens tool outputs in Snacks scratch buffers

local M = {}
local markers = require("anya.markers")
local ui_utils = require("anya.ui_utils")

--- Open tool output in a Snacks scratch buffer
--- @param output_id string The tool output hashid
--- @param tool_name string The tool name for the buffer title
function M.open_tool_output(output_id, tool_name)
  -- Fetch content from daemon via RPC
  local ok, result = pcall(vim.fn.AnyaGetToolOutput, output_id)
  if not ok then
    local err_msg = type(result) == "string" and result or "unknown error"
    vim.notify("Anya: Failed to fetch tool output: " .. err_msg, vim.log.levels.ERROR)
    return
  end
  -- Handle nil/vim.NIL (Python None becomes vim.NIL userdata in Lua)
  if result == nil or result == vim.NIL then
    vim.notify("Anya: Tool output '" .. output_id .. "' not found in database", vim.log.levels.WARN)
    return
  end
  -- Handle case where result is a table but content is missing
  if type(result) ~= "table" or not result.content or result.content == vim.NIL then
    vim.notify("Anya: Tool output '" .. output_id .. "' has no content", vim.log.levels.WARN)
    return
  end

  local content = result.content
  local filetype = result.filetype
  if filetype == vim.NIL then
    filetype = "text"
  end
  local lines = vim.split(content, "\n", { plain = true })

  -- Check if Snacks is available
  local snacks_ok, Snacks = pcall(require, "snacks")
  if not snacks_ok or not Snacks.scratch then
    -- Fallback: open in a simple scratch buffer
    M._open_simple_scratch(lines, tool_name, filetype)
    return
  end

  -- Open Snacks scratch buffer
  local win = Snacks.scratch.open({
    name = "Tool Output: " .. tool_name,
    ft = filetype,
    icon = ui_utils.icons.tool_output,
    autowrite = false,
    filekey = {
      id = output_id,
      cwd = false,
      branch = false,
      count = false,
    },
    win = {
      style = "scratch",
      wo = { winhighlight = "NormalFloat:Normal" },
      bo = { buftype = "nofile", bufhidden = "hide", swapfile = false, filetype = filetype },
    },
  })

  -- Set content and make read-only
  if win and win.buf then
    vim.bo[win.buf].modifiable = true
    vim.api.nvim_buf_set_lines(win.buf, 0, -1, false, lines)
    vim.bo[win.buf].modifiable = false
    vim.bo[win.buf].readonly = true
  end
end

--- Fallback: open in a simple split buffer
--- @param lines string[] Content lines
--- @param tool_name string Tool name for title
--- @param filetype string Filetype for syntax
function M._open_simple_scratch(lines, tool_name, filetype)
  -- Create a new scratch buffer
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
  vim.bo[buf].modifiable = false
  vim.bo[buf].readonly = true
  vim.bo[buf].buftype = "nofile"
  vim.bo[buf].bufhidden = "wipe"
  vim.bo[buf].filetype = filetype

  -- Open in a vertical split
  vim.cmd("vsplit")
  vim.api.nvim_win_set_buf(0, buf)

  -- Set buffer name
  vim.api.nvim_buf_set_name(buf, "Tool Output: " .. tool_name)

  -- Add 'q' keymap to close
  vim.keymap.set("n", "q", "<cmd>close<cr>", { buffer = buf, desc = "Close tool output" })
end

--- Check if cursor is on or near a tool output marker and open it if so
--- @return boolean True if a tool output was opened, false otherwise
function M.open_at_cursor()
  local bufnr = vim.api.nvim_get_current_buf()
  local cursor = vim.api.nvim_win_get_cursor(0)
  local line_num = cursor[1]

  -- Get current line and nearby lines (tool header might be above the marker)
  local start_line = math.max(0, line_num - 3)
  local end_line = math.min(vim.api.nvim_buf_line_count(bufnr), line_num + 2)
  local lines = vim.api.nvim_buf_get_lines(bufnr, start_line, end_line, false)

  -- Check the current line first
  local current_line_idx = line_num - start_line
  if current_line_idx >= 1 and current_line_idx <= #lines then
    local line = lines[current_line_idx]
    -- DEBUG: uncomment to see what line is being checked
    -- vim.notify("open_at_cursor: line_num=" .. line_num .. " idx=" .. current_line_idx .. " line='" .. line:sub(1, 80) .. "'", vim.log.levels.INFO)
    if markers.is_tool_output_marker(line) then
      local info = markers.parse_tool_output_marker(line)
      if info then
        M.open_tool_output(info.id, info.tool_name)
        return true
      end
    end
  end

  -- Also check lines immediately following cursor (in case cursor is on tool header)
  for i = current_line_idx + 1, math.min(current_line_idx + 2, #lines) do
    local line = lines[i]
    if markers.is_tool_output_marker(line) then
      local info = markers.parse_tool_output_marker(line)
      if info then
        M.open_tool_output(info.id, info.tool_name)
        return true
      end
    end
    -- Stop if we hit a non-marker, non-empty line
    if not markers.is_marker_line(line) and line:match("%S") then
      break
    end
  end

  return false
end

return M
