---@class agent_nvim.History
---@field path string
---@field data table[]
---@field idx number
---@field cursor number
---@field path string
local M = {}
M.__index = M

-- Save history on exit
vim.api.nvim_create_autocmd("ExitPre", {
  group = vim.api.nvim_create_augroup("agent_nvim_history", { clear = true }),
  callback = function()
    -- Try to save the global history instance if it exists
    if _G.agent_prompt_history and _G.agent_prompt_history.save then
      pcall(_G.agent_prompt_history.save, _G.agent_prompt_history)
    end
  end,
})

-- Initialize history module
function M.new()
  local self = setmetatable({}, M)

  -- Ensure data directory exists
  local data_dir = vim.fn.stdpath("data") .. "/agent.nvim"
  vim.fn.mkdir(data_dir, "p")

  self.path = data_dir .. "/prompt_history.txt"
  self.data = {}
  self.idx = 0
  self.cursor = 0

  -- Load existing history
  self:load()

  return self
end

-- Load history from file
function M:load()
  local file = io.open(self.path, "r")
  if not file then
    return
  end

  self.data = {}
  for line in file:lines() do
    -- Skip empty lines and comments
    if line ~= "" and not line:match("^#") then
      table.insert(self.data, line)
    end
  end
  file:close()

  self.idx = #self.data
  self.cursor = self.idx
end

-- Save history to file
function M:save()
  local file, err = io.open(self.path, "w")
  if not file then
    vim.notify("Failed to save agent prompt history: " .. (err or "unknown error"), vim.log.levels.ERROR)
    return false
  end

  local success, write_err = pcall(function()
    -- Write header comment
    file:write("# Agent.nvim Prompt History\n")
    file:write("# Format: one prompt per line\n")
    file:write("# Lines starting with # are comments\n\n")

    -- Write all prompts
    for _, prompt in ipairs(self.data) do
      file:write(prompt .. "\n")
    end
  end)

  file:close()

  if not success then
    vim.notify("Error writing prompt history: " .. (write_err or "unknown error"), vim.log.levels.ERROR)
    return false
  end

  return true
end

-- Check if we're at the current position (newest entry)
function M:is_current()
  return self.cursor == self.idx
end

-- Record a new prompt in history
function M:record(prompt)
  -- Trim whitespace
  prompt = vim.trim(prompt)

  -- Don't record empty prompts
  if prompt == "" then
    return false
  end

  -- Check if prompt already exists in history
  for i, existing in ipairs(self.data) do
    if existing == prompt then
      -- Remove from current position and add to end (move to top of stack)
      table.remove(self.data, i)
      break
    end
  end

  -- Add to history (limit to last 1000 entries to prevent file from growing too large)
  table.insert(self.data, prompt)
  self.idx = #self.data
  self.cursor = self.idx

  -- Trim history if it gets too large
  if #self.data > 1000 then
    table.remove(self.data, 1)
    self.idx = #self.data
    self.cursor = self.idx
  end

  -- Auto-save for safety
  local save_success = self:save()
  if not save_success then
    vim.notify("Warning: Failed to save prompt to history file", vim.log.levels.WARN)
  end

  return true
end

-- Navigate to next (newer) entry in history
-- Returns nil when at current position (caller should restore original content)
function M:next()
  if self.cursor >= self.idx then
    return nil -- At current position, caller should restore user's original text
  end
  self.cursor = self.cursor + 1
  return self:get()
end

-- Navigate to previous (older) entry in history
-- Returns nil if no history or already at oldest entry
function M:prev()
  if #self.data == 0 then
    return nil
  end
  if self.cursor <= 1 then
    return nil -- Already at oldest entry
  end
  self.cursor = self.cursor - 1
  return self:get()
end

-- Get the first (oldest) entry - for initial prev() call when at current
function M:get_oldest_from_current()
  if #self.data == 0 then
    return nil
  end
  self.cursor = self.idx -- Start from newest
  if self.cursor >= 1 then
    return self.data[self.cursor]
  end
  return nil
end

-- Get current history entry
function M:get()
  if self.cursor == 0 or self.cursor > #self.data then
    return nil
  end
  return self.data[self.cursor]
end

-- Reset cursor to current position (newest)
function M:reset()
  self.cursor = self.idx
end

-- Get all history entries
function M:get_all()
  return vim.deepcopy(self.data)
end

-- Diagnostic function to check history status
function M:diagnostic()
  local issues = {}

  -- Check if data directory exists
  local data_dir = vim.fn.stdpath("data") .. "/agent.nvim"
  if vim.fn.isdirectory(data_dir) == 0 then
    table.insert(issues, "Data directory does not exist: " .. data_dir)
  end

  -- Check if history file exists
  if vim.fn.filereadable(self.path) == 0 then
    table.insert(issues, "History file does not exist: " .. self.path)
  end

  -- Check if we can write to the history file
  local test_file, err = io.open(self.path, "a")
  if not test_file then
    table.insert(issues, "Cannot write to history file: " .. (err or "unknown error"))
  else
    test_file:close()
  end

  -- Check data integrity
  if #self.data == 0 then
    table.insert(issues, "No history entries loaded")
  end

  return {
    path = self.path,
    data_dir = data_dir,
    entry_count = #self.data,
    cursor_pos = self.cursor,
    idx = self.idx,
    issues = issues,
    status = #issues == 0 and "OK" or "ISSUES FOUND",
  }
end

return M
