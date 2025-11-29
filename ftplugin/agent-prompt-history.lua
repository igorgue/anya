-- agent-prompt.lua - Enhanced prompt buffer functionality with history
local bufnr = vim.api.nvim_get_current_buf()

-- Load history module
local history = require('agent_nvim.history')

-- Initialize global history instance
if not _G.agent_prompt_history then
  _G.agent_prompt_history = history.new()
end

local history_instance = _G.agent_prompt_history

-- Track the current buffer content for history navigation
local current_content = ""

-- Function to get current buffer content
local function get_buffer_content()
  local lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)
  return table.concat(lines, "\n")
end

-- Function to set buffer content
local function set_buffer_content(text)
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
  -- If we haven't recorded current content yet, do it now
  if history_instance:is_current() then
    current_content = get_buffer_content()
  end
  
  local prev_entry = history_instance:prev()
  if prev_entry then
    set_buffer_content(prev_entry)
  end
end

function _G.AgentHistoryNext()
  local next_entry = history_instance:next()
  if next_entry then
    set_buffer_content(next_entry)
  else
    -- We're at the newest position or beyond, restore the original content
    set_buffer_content(current_content)
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
    history_instance:record(content)
  end
  -- Reset history cursor to newest position after submitting
  history_instance:reset()
end

-- Setup autocommands for this buffer
local group = vim.api.nvim_create_augroup('AgentPromptHistory', { clear = true })

-- Reset history position when buffer is entered from normal mode
vim.api.nvim_create_autocmd('InsertEnter', {
  group = group,
  buffer = bufnr,
  callback = function()
    if not history_instance:is_current() then
      -- If we're not at current position, record current content when entering insert mode
      current_content = get_buffer_content()
      history_instance:reset()
    end
  end,
})

-- History save function that can be called from Python
function _G.AgentHistorySavePrompt(prompt_text)
  if prompt_text and prompt_text ~= "" then
    history_instance:record(prompt_text)
    -- Reset history cursor to newest position after submitting
    history_instance:reset()
  end
end

-- Export functions for global access
_G.AgentPromptHistory = {
  prev = _G.AgentHistoryPrev,
  next = _G.AgentHistoryNext,
  submit = _G.AgentHistorySubmit,
  instance = history_instance,
}