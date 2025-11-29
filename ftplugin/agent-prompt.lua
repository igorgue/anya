-- Placeholder functionality for agent prompt buffer
local bufnr = vim.api.nvim_get_current_buf()
local placeholder_ns = vim.api.nvim_create_namespace('agent_prompt_placeholder')

-- Global placeholder text and highlight
_G.agent_prompt_placeholder = 'type `:qa!` to exit'
_G.agent_prompt_highlight = 'Comment'
_G.placeholder_mark_id = nil

-- Function to update placeholder visibility
local function update_placeholder()
  -- Clear old mark if exists
  if _G.placeholder_mark_id then
    pcall(vim.api.nvim_buf_del_extmark, bufnr, placeholder_ns, _G.placeholder_mark_id)
    _G.placeholder_mark_id = nil
  end

  local line_count = vim.api.nvim_buf_line_count(bufnr)
  local first_line = vim.api.nvim_buf_get_lines(bufnr, 0, 1, false)[1] or ''

  if line_count == 1 and first_line == '' then
    _G.placeholder_mark_id = vim.api.nvim_buf_set_extmark(bufnr, placeholder_ns, 0, 0, {
      virt_text = {{_G.agent_prompt_placeholder, _G.agent_prompt_highlight}},
      virt_text_pos = 'inline',
      virt_text_win_col = vim.fn.winwidth(0) - string.len(_G.agent_prompt_placeholder)
    })
  end
end

-- Global function to change placeholder text and highlight
function _G.AgentSetPlaceholder(text, highlight)
  _G.agent_prompt_placeholder = text
  _G.agent_prompt_highlight = highlight or 'Comment'
  update_placeholder()
end

-- Attach to buffer to update placeholder on text changes
local bufnr = vim.api.nvim_get_current_buf()
vim.api.nvim_buf_attach(bufnr, false, {
  on_lines = function()
    update_placeholder()
  end
})

-- Initial placeholder display
update_placeholder()
