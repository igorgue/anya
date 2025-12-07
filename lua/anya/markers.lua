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

-- Pattern to match message markers: <!-- am: id, start/end, ... -->
-- User message: <!-- am: 604c2d, start, Igor, 2024-06-27T14:30:00Z -->
-- Agent message: <!-- am: f13e20, start, code, gpt-4.1, 2024-06-27T14:30:00Z -->
M.MESSAGE_PATTERN = "^<!%-%- am: (.+) %-%->$"

-- Pattern to match conversation markers: <!-- ac: id, timestamp -->
M.CONVERSATION_PATTERN = "^<!%-%- ac: (.+) %-%->$"

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

--- Parse a message marker line and return its components
--- User message format: <!-- am: id, start, name, timestamp -->
--- Agent message format: <!-- am: id, start, agent_type, model, timestamp -->
--- End format: <!-- am: id, end, timestamp -->
--- @param line string The line to parse
--- @return table|nil Parsed info: { id, type, is_agent, name/agent_type, model, timestamp }
function M.parse_message_marker(line)
  local content = line:match(M.MESSAGE_PATTERN)
  if not content then
    return nil
  end

  local parts = {}
  for part in content:gmatch("([^,]+)") do
    part = part:match("^%s*(.-)%s*$") -- trim whitespace
    if part ~= "" then
      table.insert(parts, part)
    end
  end

  if #parts < 3 then
    return nil
  end

  local result = {
    id = parts[1],
    type = parts[2], -- "start" or "end"
  }

  if result.type == "start" then
    -- Determine if agent or user message by field count
    -- User: id, start, name, timestamp (4 fields)
    -- Agent: id, start, agent_type, model, timestamp (5 fields)
    if #parts == 5 then
      -- Agent message
      result.is_agent = true
      result.agent_type = parts[3]
      result.model = parts[4]
      result.timestamp = parts[5]
    elseif #parts == 4 then
      -- User message
      result.is_agent = false
      result.name = parts[3]
      result.timestamp = parts[4]
    else
      return nil
    end
  elseif result.type == "end" then
    -- End marker: id, end, timestamp
    result.timestamp = parts[3]
  else
    return nil
  end

  return result
end

--- Create a conversation marker line
--- @param id string The conversation ID
--- @param timestamp string ISO 8601 UTC timestamp
--- @return string A marker line like '<!-- ac: 67f169, 2024-06-27T14:30:00Z -->'
function M.make_conversation_marker(id, timestamp)
  return "<!-- ac: " .. id .. ", " .. timestamp .. " -->"
end

--- Create a message start marker line for a user message
--- @param id string The message ID
--- @param name string The user's name
--- @param timestamp string ISO 8601 UTC timestamp
--- @return string A marker line like '<!-- am: 604c2d, start, Igor, 2024-06-27T14:30:00Z -->'
function M.make_user_message_start(id, name, timestamp)
  return "<!-- am: " .. id .. ", start, " .. name .. ", " .. timestamp .. " -->"
end

--- Create a message end marker line
--- @param id string The message ID
--- @param timestamp string ISO 8601 UTC timestamp
--- @return string A marker line like '<!-- am: 604c2d, end, 2024-06-27T14:30:00Z -->'
function M.make_message_end(id, timestamp)
  return "<!-- am: " .. id .. ", end, " .. timestamp .. " -->"
end

--- Create a message start marker line for an agent message
--- @param id string The message ID
--- @param agent_type string The agent type (e.g., "code", "plan")
--- @param model string The model name (e.g., "gpt-4.1")
--- @param timestamp string ISO 8601 UTC timestamp
--- @return string A marker line like '<!-- am: f13e20, start, code, gpt-4.1, 2024-06-27T14:30:00Z -->'
function M.make_agent_message_start(id, agent_type, model, timestamp)
  return "<!-- am: " .. id .. ", start, " .. agent_type .. ", " .. model .. ", " .. timestamp .. " -->"
end

--- Check if a line is a conversation marker line
--- @param line string The line to check
--- @return boolean True if the line is a conversation marker line
function M.is_conversation_marker(line)
  return line:match(M.CONVERSATION_PATTERN) ~= nil
end

--- Parse a conversation marker line and return its components
--- Format: <!-- ac: id, timestamp -->
--- @param line string The line to parse
--- @return table|nil Parsed conversation info: { id, timestamp }
function M.parse_conversation_marker(line)
  local content = line:match(M.CONVERSATION_PATTERN)
  if not content then
    return nil
  end

  local parts = {}
  for part in content:gmatch("([^,]+)") do
    part = part:match("^%s*(.-)%s*$") -- trim whitespace
    if part ~= "" then
      table.insert(parts, part)
    end
  end

  if #parts < 2 then
    return nil
  end

  return {
    id = parts[1],
    timestamp = parts[2],
  }
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
