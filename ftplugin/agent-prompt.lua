-- ftplugin/agent-prompt.lua
local bufnr = vim.api.nvim_get_current_buf()
local placeholder_ns = vim.api.nvim_create_namespace('agent_prompt_placeholder')

-- Global placeholder text and highlight
_G.agent_prompt_placeholder = 'type `:qa!` to exit'
_G.agent_prompt_highlight = 'Comment'

-- Function to update placeholder visibility
local function update_placeholder()
  local first_line = vim.api.nvim_buf_get_lines(bufnr, 0, 1, false)[1] or ''
  local line_count = vim.api.nvim_buf_line_count(bufnr)

  -- Always clear old placeholder first
  vim.api.nvim_buf_clear_namespace(bufnr, placeholder_ns, 0, -1)

  if line_count == 1 and first_line == '' then
    vim.api.nvim_buf_set_extmark(bufnr, placeholder_ns, 0, 0, {
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

-- Update placeholder on text changes
local group = vim.api.nvim_create_augroup('AgentPromptPlaceholder', { clear = true })
vim.api.nvim_create_autocmd('TextChanged', {
  group = group,
  buffer = bufnr,
  callback = update_placeholder,
})

vim.api.nvim_create_autocmd('TextChangedI', {
  group = group,
  buffer = bufnr,
  callback = update_placeholder,
})

-- Initial update
update_placeholder()
