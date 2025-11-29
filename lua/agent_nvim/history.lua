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
    M.save()
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
  local file = io.open(self.path, "w")
  if not file then
    vim.notify("Failed to save agent prompt history", vim.log.levels.ERROR)
    return
  end
  
  -- Write header comment
  file:write("# Agent.nvim Prompt History\n")
  file:write("# Format: one prompt per line\n")
  file:write("# Lines starting with # are comments\n\n")
  
  -- Write all prompts
  for _, prompt in ipairs(self.data) do
    file:write(prompt .. "\n")
  end
  
  file:close()
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
  
  -- Don't record if it's identical to the last recorded prompt
  if self.idx > 0 and self.data[self.idx] == prompt then
    return false
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
  self:save()
  
  return true
end

-- Navigate to next (newer) entry in history
function M:next()
  if self.cursor >= self.idx then
    return nil
  end
  self.cursor = self.cursor + 1
  return self:get()
end

-- Navigate to previous (older) entry in history  
function M:prev()
  self.cursor = math.max(self.cursor - 1, 1)
  return self:get()
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

return M