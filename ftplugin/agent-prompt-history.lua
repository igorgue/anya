-- agent-prompt.lua - Enhanced prompt buffer functionality with history
local bufnr = vim.api.nvim_get_current_buf()

-- Ensure buffer is modifiable for history navigation
vim.api.nvim_set_option_value('modifiable', true, { buf = bufnr })

-- Load history module
local history = require('agent_nvim.history')

-- Initialize global history instance
if not _G.agent_prompt_history then
  _G.agent_prompt_history = history.new()
end

-- Helper to always get the current global instance (in case it gets reinitialized)
local function get_history()
  return _G.agent_prompt_history
end

-- Track the current buffer content for history navigation
local original_content = ""  -- What user was typing before navigating
local is_navigating = false  -- Whether we're in history navigation mode

-- Function to get current buffer content
local function get_buffer_content()
  if not vim.api.nvim_buf_is_valid(bufnr) then
    return ""
  end
  local lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)
  return table.concat(lines, "\n")
end

-- Function to set buffer content
local function set_buffer_content(text)
  -- Ensure buffer is still valid and modifiable
  if not vim.api.nvim_buf_is_valid(bufnr) then
    return
  end
  
  -- Make buffer modifiable if needed
  local modifiable = vim.api.nvim_buf_get_option(bufnr, 'modifiable')
  if not modifiable then
    vim.api.nvim_set_option_value('modifiable', true, { buf = bufnr })
  end
  
  -- Split text into lines
  local lines = {}
  for line in (text .. "\n"):gmatch("(.-)\n") do
    table.insert(lines, line)
  end
  
  -- Set buffer lines
  vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, lines)
  
  -- Move cursor to end of text
  local last_line = math.max(1, #lines)
  local last_col = #lines[#lines] or 0
  vim.api.nvim_win_set_cursor(0, {last_line, last_col})
  
  -- Restore original modifiable state
  if not modifiable then
    vim.api.nvim_set_option_value('modifiable', false, { buf = bufnr })
  end
end

-- Function to record current buffer content before navigating history
local function record_current()
  local content = get_buffer_content()
  if content ~= "" then
    current_content = content
    history_instance:record(content)
  end
end

-- History navigation functions (normal mode only)
function _G.AgentHistoryPrev()
  -- Ensure we have a valid buffer
  local current_buf = vim.api.nvim_get_current_buf()
  if not vim.api.nvim_buf_is_valid(current_buf) then
    return
  end
  
  -- First time pressing prev: save current content and show newest history entry
  if not is_navigating then
    original_content = get_buffer_content()
    is_navigating = true
    -- Get the newest history entry first
    local entry = get_history():get_oldest_from_current()
    if entry then
      set_buffer_content(entry)
    end
    return
  end
  
  -- Already navigating: go to older entry
  local prev_entry = get_history():prev()
  if prev_entry then
    set_buffer_content(prev_entry)
  end
  -- If nil, we're at oldest - do nothing (stay on current entry)
end

function _G.AgentHistoryNext()
  -- Ensure we have a valid buffer
  local current_buf = vim.api.nvim_get_current_buf()
  if not vim.api.nvim_buf_is_valid(current_buf) then
    return
  end
  
  -- Not navigating? Nothing to do
  if not is_navigating then
    return
  end
  
  local next_entry = get_history():next()
  if next_entry then
    set_buffer_content(next_entry)
  else
    -- We've gone past the newest entry - restore original content
    set_buffer_content(original_content)
    original_content = ""
    is_navigating = false
    get_history():reset()
  end
end

-- Function to call from Vimscript
function _G.AgentHistoryPrevVim()
  _G.AgentHistoryPrev()
end

function _G.AgentHistoryNextVim()
  _G.AgentHistoryNext()
end

-- Function to save current prompt on submit
function _G.AgentHistorySubmit()
  local content = get_buffer_content()
  if content ~= "" then
    get_history():record(content)
  end
  -- Reset history cursor to newest position after submitting
  get_history():reset()
  -- Reset navigation state
  original_content = ""
  is_navigating = false
end

-- Setup autocommands for this buffer
local group = vim.api.nvim_create_augroup('AgentPromptHistory', { clear = true })

-- Only reset navigation state when entering insert mode (user is typing new content)
vim.api.nvim_create_autocmd('InsertEnter', {
  group = group,
  buffer = bufnr,
  callback = function()
    -- If user enters insert mode, they're done navigating
    if is_navigating then
      -- Keep whatever content is showing (they chose it)
      original_content = ""
      is_navigating = false
      get_history():reset()
    end
  end,
})

-- History save function that can be called from Python
function _G.AgentHistorySavePrompt(prompt_text)
  if prompt_text and prompt_text ~= "" then
    local success = get_history():record(prompt_text)
    -- Reset history cursor to newest position after submitting
    get_history():reset()
    return success
  end
  return false
end

-- Export functions for global access
_G.AgentPromptHistory = {
  prev = _G.AgentHistoryPrev,
  next = _G.AgentHistoryNext,
  submit = _G.AgentHistorySubmit,
  diagnostic = function()
    return get_history():diagnostic()
  end,
  get_history = get_history,
}