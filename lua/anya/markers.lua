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

--- Check if a line contains a specific marker
--- @param line string The line to check
--- @param marker_name string The marker name to look for (e.g., "tool_pending")
--- @return boolean True if the line is a marker line containing the specified marker
function M.has_marker(line, marker_name)
  local parsed = M.parse_marker(line)
  if not parsed then
    return false
  end
  for _, m in ipairs(parsed) do
    if m == marker_name then
      return true
    end
  end
  return false
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

--- Ensure all markers in the text are on their own lines
--- @param text string The text to process
--- @return string Text with markers isolated on separate lines
function M.ensure_marker_line_isolation(text)
  if not text or text == "" then
    return text
  end

  if not text:find("<!%-%- a[tmo][mot]?:") then
    return text
  end

  local lines = vim.split(text, "\n", { plain = true })
  local normalized_lines = {}

  for _, line in ipairs(lines) do
    local stripped = vim.trim(line)

    -- Check if line contains a marker pattern
    local contains_at_marker = line:find("<!%-%- at:") ~= nil
    local contains_am_marker = line:find("<!%-%- am:") ~= nil
    local contains_ato_marker = line:find("<!%-%- ato:") ~= nil
    local contains_marker = contains_at_marker or contains_am_marker or contains_ato_marker

    if contains_marker then
      -- Check if stripped line IS a marker (marker already isolated)
      local is_at = M.is_marker_line(stripped)
      local is_am = M.is_message_marker(stripped)
      local is_ato = M.is_tool_output_marker(stripped)
      local is_marker_line = is_at or is_am or is_ato

      if is_marker_line then
        table.insert(normalized_lines, stripped)
      else
        local start_marker = line:find("<!--", 1, true)
        local end_marker = line:find("-->", start_marker or 1, true)

        if start_marker and end_marker then
          local before = line:sub(1, start_marker - 1)
          local marker_text = line:sub(start_marker, end_marker + 2)
          local after = line:sub(end_marker + 3)

          if #before > 0 then
            table.insert(normalized_lines, before)
          end
          table.insert(normalized_lines, vim.trim(marker_text))
          if #after > 0 then
            table.insert(normalized_lines, after)
          end
        else
          table.insert(normalized_lines, line)
        end
      end
    else
      table.insert(normalized_lines, line)
    end
  end

  local result = table.concat(normalized_lines, "\n")

  -- Remove blank lines around markers (markers should be on their own line, but without extra blank lines)
  -- This aligns with the Python spacing.py ensure_marker_isolation behavior
  -- NOTE: Do NOT remove blank lines before tool markers (fold_start etc) - Python spacing
  -- manager intentionally adds blank lines before tool calls for readability.
  -- Only remove blank lines before MESSAGE markers (am:)
  result = result:gsub("\n%s*\n+(<!%-%- am: .+ %-%->)", "\n%1")
  -- Remove extra blank lines after markers (keep only one newline)
  result = result:gsub("(<!%-%- at: .+ %-%->)%s*\n%s*\n+", "%1\n")
  result = result:gsub("(<!%-%- am: .+ %-%->)%s*\n%s*\n+", "%1\n")
  result = result:gsub("(<!%-%- ato: .+ %-%->)%s*\n%s*\n+", "%1\n")
  -- Ensure adjacent markers have only one newline between them
  result = result:gsub("(<!%-%- a[mt]o?: .+ %-%->)%s*\n%s*(<!%-%- a[mt]o?: .+ %-%->)", "%1\n%2")

  -- Final normalization: collapse multiple newlines (reduce 3+ to 2)
  result = result:gsub("\n\n\n+", "\n\n")

  return result
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

-- Tool output marker pattern: <!-- ato: id, tool_name, line_count -->
-- Can be standalone or embedded in a line (e.g., appended to header)
M.TOOL_OUTPUT_PATTERN = "<!%-%- ato: ([^,]+), ([^,]+), (%d+) %-%->"
M.TOOL_OUTPUT_PREFIX = "<!-- ato:"

--- Parse a tool output marker from a line (can be embedded anywhere in the line)
--- @param line string The line to parse
--- @return table|nil Parsed info: { id = string, tool_name = string, line_count = number }
function M.parse_tool_output_marker(line)
  local id, tool_name, line_count = line:match(M.TOOL_OUTPUT_PATTERN)
  if id and tool_name and line_count then
    return { id = vim.trim(id), tool_name = vim.trim(tool_name), line_count = tonumber(line_count) }
  end
  return nil
end

--- Check if a line contains a tool output marker
--- @param line string The line to check
--- @return boolean True if the line contains a tool output marker
function M.is_tool_output_marker(line)
  return line:find(M.TOOL_OUTPUT_PREFIX, 1, true) ~= nil
end

return M
