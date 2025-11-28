-- Placeholder functionality for agent prompt buffer
local bufnr = vim.api.nvim_get_current_buf()
local placeholder_ns = vim.api.nvim_create_namespace('agent_prompt_placeholder')

-- Global placeholder text
_G.agent_prompt_placeholder = '...'

-- Function to update placeholder visibility
local function update_placeholder()
  vim.api.nvim_buf_clear_namespace(bufnr, placeholder_ns, 0, -1)
  
  local line_count = vim.api.nvim_buf_line_count(bufnr)
  local first_line = vim.api.nvim_buf_get_lines(bufnr, 0, 1, false)[1] or ''
  
  if line_count == 1 and first_line == '' then
    vim.api.nvim_buf_set_extmark(bufnr, placeholder_ns, 0, 0, {
      virt_text = {{_G.agent_prompt_placeholder, 'Comment'}},
      virt_text_pos = 'inline'
    })
  end
end

-- Global function to change placeholder text
function _G.AgentSetPlaceholder(text)
  _G.agent_prompt_placeholder = text
  update_placeholder()
end

-- Attach to buffer to update placeholder on text changes
vim.api.nvim_buf_attach(bufnr, false, {
  on_lines = function()
    update_placeholder()
  end
})

-- Initial placeholder display
update_placeholder()
