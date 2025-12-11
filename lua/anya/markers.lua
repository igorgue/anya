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

M.PREFIX = "<!-- at:"
M.SUFFIX = "-->"

-- Pattern to match tool marker lines: <!-- at: ... -->
M.TOOLS_PATTERN = "^<!%-%- at: (.+) %-%->$"

-- Pattern to match simplified message markers: <!-- am: id -->
M.MESSAGE_PATTERN = "^<!%-%- am: (.+) %-%->$"

--- Create a marker line with the given marker names
--- @param ... string One or more marker names to include
--- @return string A marker line like '<!-- at: fold_start, tool_pending -->'
function M.make_marker(...)
  local names = { ... }
  return M.PREFIX .. " " .. table.concat(names, ", ") .. " " .. M.SUFFIX
end

--- Parse a marker line and return the marker names
--- @param line string The line to parse
--- @return string[]|nil List of marker names, or nil if not a marker line
function M.parse_marker(line)
  local content = line:match(M.TOOLS_PATTERN)
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
  return line:match(M.TOOLS_PATTERN) ~= nil
end

--- Check if a line is a message marker line
--- @param line string The line to check
--- @return boolean True if the line is a message marker line
function M.is_message_marker(line)
  return line:match(M.MESSAGE_PATTERN) ~= nil
end

--- Parse a message marker line and return the message ID
--- @param line string The line to parse
--- @return table|nil Parsed info: { id = string }
function M.parse_message_marker(line)
  local content = line:match(M.MESSAGE_PATTERN)
  if not content then
    return nil
  end

  content = vim.trim(content)
  if content == "" then
    return nil
  end

  return { id = content }
end

--- Create a simplified message marker line
--- @param id string The message ID
--- @return string Marker line like '<!-- am: 604c2d -->'
function M.make_message_marker(id)
  return "<!-- am: " .. id .. " -->"
end

--- Convert ISO 8601 UTC timestamp to local time string (e.g., "2:30pm")
--- @param iso_timestamp string ISO 8601 timestamp like "2024-06-27T14:30:00Z"
--- @return string Local time formatted as "2:30pm"
function M.utc_to_local_time(iso_timestamp)
  -- Parse ISO 8601 format: YYYY-MM-DDTHH:MM:SSZ
  local year, month, day, hour, min, sec = iso_timestamp:match("(%d+)-(%d+)-(%d+)T(%d+):(%d+):(%d+)")

  if not year then
    return iso_timestamp -- Return original if parsing fails
  end

  -- Create UTC time table
  local utc_time = os.time({
    ---@diagnostic disable-next-line: assign-type-mismatch
    year = tonumber(year),
    ---@diagnostic disable-next-line: assign-type-mismatch
    month = tonumber(month),
    ---@diagnostic disable-next-line: assign-type-mismatch
    day = tonumber(day),
    hour = tonumber(hour),
    min = tonumber(min),
    sec = tonumber(sec),
    isdst = false,
  })

  -- Get local timezone offset by comparing local and UTC representations
  local local_time = os.date("*t", utc_time)
  local utc_table = os.date("!*t", utc_time)

  -- Calculate offset in seconds
  ---@diagnostic disable-next-line: param-type-mismatch
  local local_ts = os.time(local_time)
  ---@diagnostic disable-next-line: param-type-mismatch
  local utc_ts = os.time(utc_table)
  local offset = local_ts - utc_ts

  -- Apply offset to get local time
  local local_timestamp = utc_time + offset

  -- Format as "2:30pm"
  local local_date = os.date("*t", local_timestamp)
  local hour_12 = local_date.hour % 12
  if hour_12 == 0 then
    hour_12 = 12
  end
  local ampm = local_date.hour >= 12 and "pm" or "am"

  return string.format("%d:%02d%s", hour_12, local_date.min, ampm)
end

return M
