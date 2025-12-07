-- Prompt history management for Anya plugin
-- Stores and retrieves previously sent prompts

local M = {}

-- Configuration
local MAX_ENTRIES = 1000
local history_dir = os.getenv("XDG_DATA_HOME") or (vim.fn.expand("$HOME") .. "/.local/share")
local HISTORY_FILE = history_dir .. "/anya/prompt_history.txt"
local PROMPT_SEPARATOR = "---ANYA_PROMPT_SEPARATOR---"

-- History state
local history = {}
local is_loaded = false
local is_navigating = false
local history_index = nil -- nil means newest, number means index in history
local original_content = ""

-- Ensure data directory exists
local function ensure_data_dir()
  local data_dir = history_dir .. "/anya"
  if vim.fn.isdirectory(data_dir) == 0 then
    vim.fn.mkdir(data_dir, "p")
  end
end

-- Load history from file
function M.load()
  if is_loaded then
    return
  end

  ensure_data_dir()

  if vim.fn.filereadable(HISTORY_FILE) == 0 then
    history = {}
    is_loaded = true
    return
  end

  local content = vim.fn.readfile(HISTORY_FILE)
  history = {}
  local current_entry = ""

  for _, line in ipairs(content) do
    if line == PROMPT_SEPARATOR then
      if current_entry ~= "" then
        table.insert(history, current_entry)
        current_entry = ""
      end
    else
      if current_entry ~= "" then
        current_entry = current_entry .. "\n"
      end
      current_entry = current_entry .. line
    end
  end

  -- Don't forget the last entry if file doesn't end with separator
  if current_entry ~= "" then
    table.insert(history, current_entry)
  end

  is_loaded = true
end

-- Save history to file
function M.save()
  ensure_data_dir()

  local lines = {}
  for _, entry in ipairs(history) do
    -- Split entry into lines and add separator
    local entry_lines = vim.split(entry, "\n", { plain = true })
    for _, line in ipairs(entry_lines) do
      table.insert(lines, line)
    end
    table.insert(lines, PROMPT_SEPARATOR)
  end

  vim.fn.writefile(lines, HISTORY_FILE)
end

-- Add a prompt to history
-- @param prompt string The prompt to add
function M.add(prompt)
  M.load()

  -- Remove trailing newlines from prompt
  prompt = prompt:gsub("\n+$", "")

  -- Skip empty prompts
  if prompt == "" then
    return
  end

  -- Check if prompt already exists in history (most recent first)
  for i = #history, 1, -1 do
    if history[i] == prompt then
      -- Move existing prompt to the top (most recent)
      table.remove(history, i)
      break
    end
  end

  -- Add to beginning (most recent)
  table.insert(history, 1, prompt)

  -- Trim history if it exceeds maximum entries
  while #history > MAX_ENTRIES do
    table.remove(history, #history)
  end

  M.save()

  -- Reset navigation state
  is_navigating = false
  history_index = nil
end

-- Get a prompt from history by index (1 = most recent)
-- @param index number The index in history
-- @return string|nil The prompt or nil if index out of bounds
function M.get(index)
  M.load()
  if index < 1 or index > #history then
    return nil
  end
  return history[index]
end

-- Get the current history state
-- @return table { history: table, is_navigating: boolean, index: number|nil, original: string }
function M.get_state()
  return {
    history = vim.deepcopy(history),
    is_navigating = is_navigating,
    index = history_index,
    original = original_content,
  }
end

-- Start navigating history
-- @param original string The current buffer content before navigation started
function M.start_navigation(original)
  M.load()
  is_navigating = true
  history_index = nil
  original_content = original
end

-- Stop navigating history
function M.stop_navigation()
  is_navigating = false
  history_index = nil
  original_content = ""
end

-- Navigate to previous (older) prompt
-- @return string|nil The previous prompt or nil if at the oldest
function M.navigate_previous()
  if not is_navigating then
    return nil
  end

  M.load()

  -- If at newest (index is nil), show the most recent
  if history_index == nil then
    if #history > 0 then
      history_index = 1
      return history[1]
    end
    return nil
  end

  -- Move to older prompt
  if history_index < #history then
    history_index = history_index + 1
    return history[history_index]
  end

  return nil -- Already at the oldest
end

-- Navigate to next (newer) prompt
-- @return string|nil The next prompt or the original content if past newest
function M.navigate_next()
  if not is_navigating then
    return nil
  end

  if history_index == nil then
    return nil -- Already at newest
  end

  -- Move to newer prompt
  if history_index > 1 then
    history_index = history_index - 1
    return history[history_index]
  else
    -- Past newest, return to original content
    history_index = nil
    return original_content
  end
end

-- Check if currently navigating history
-- @return boolean True if navigating
function M.is_navigating()
  return is_navigating
end

return M
