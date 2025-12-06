-- Text markers for embedding metadata in buffer content.
-- These HTML comment markers are invisible in markdown renderers and allow
-- reconstructing UI state (folds, extmarks, widgets) from pure text content.
-- They are human-readable and safe to edit.

local M = {}

-- Marker names
M.fold_start = "fold_start"
M.fold_end = "fold_end"
M.tool_pending = "tool_pending"
M.tool_success = "tool_success"
M.tool_failure = "tool_failure"
M.edit_pending = "edit_pending"
M.edit_applied = "edit_applied"
M.edit_rejected = "edit_rejected"
M.edit_failed = "edit_failed"
M.thinking = "thinking"

M.PREFIX = "<!-- anya:"
M.SUFFIX = "-->"

-- Pattern to match marker lines: <!-- anya: ... -->
M.PATTERN = "^<!%-%- anya: (.+) %-%->$"

--- Create a marker line with the given marker names
--- @param ... string One or more marker names to include
--- @return string A marker line like '<!-- anya: fold_start, tool_pending -->'
function M.make_marker(...)
  local names = { ... }
  return M.PREFIX .. " " .. table.concat(names, ", ") .. " " .. M.SUFFIX
end

--- Parse a marker line and return the marker names
--- @param line string The line to parse
--- @return string[]|nil List of marker names, or nil if not a marker line
function M.parse_marker(line)
  local content = line:match(M.PATTERN)
  if not content then
    return nil
  end

  local names = {}
  for name in content:gmatch("([^,]+)") do
    -- Trim whitespace
    name = name:match("^%s*(.-)%s*$")
    if name ~= "" then
      table.insert(names, name)
    end
  end

  return names
end

--- Check if a line is a marker line
--- @param line string The line to check
--- @return boolean True if the line is a marker line
function M.is_marker_line(line)
  return line:match(M.PATTERN) ~= nil
end

return M
