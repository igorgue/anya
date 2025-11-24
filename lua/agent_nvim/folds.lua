local M = {}

-- Storage for fold summaries: bufnr -> { [start_line] = summary_text }
M.fold_summaries = {}

-- Namespace for fold extmarks
local NS_FOLD = vim.api.nvim_create_namespace('agent_nvim_folds')

---Initialize fold settings for a buffer
---@param bufnr number Buffer number
function M.setup(bufnr)
  if not vim.api.nvim_buf_is_valid(bufnr) then
    return
  end
  
  -- Ensure manual folding
  vim.api.nvim_buf_call(bufnr, function()
    vim.wo.foldmethod = 'manual'
  end)
end

---Global function called by Neovim to display folded text
---@return string
function M.fold_text()
  local bufnr = vim.api.nvim_get_current_buf()
  local start = vim.v.foldstart - 1
  local folds = M.fold_summaries[bufnr] or {}
  local summary = folds[start]
  
  if not summary then
    return '  ... folded ...'
  end
  
  return '  ' .. summary
end

---Create a fold for tool call or result
---@param bufnr number Buffer number
---@param start_row number 0-based start line
---@param end_row number 0-based end line
---@param summary string Summary text to show when folded
function M.create_fold(bufnr, start_row, end_row, summary)
  if not vim.api.nvim_buf_is_valid(bufnr) then
    return
  end
  
  -- Don't fold single lines
  if start_row >= end_row then
    return
  end
  
  -- Store fold summary
  M.fold_summaries[bufnr] = M.fold_summaries[bufnr] or {}
  M.fold_summaries[bufnr][start_row] = summary
  
  -- Create the fold
  local ok, err = pcall(function()
    vim.api.nvim_buf_call(bufnr, function()
      vim.cmd(string.format('%d,%dfold', start_row + 1, end_row + 1))
    end)
  end)
  
  if not ok then
    vim.notify('Failed to create fold: ' .. tostring(err), vim.log.levels.DEBUG)
  end
end

---Delete a fold at the specified line
---@param bufnr number Buffer number
---@param line number 0-based line number
function M.delete_fold(bufnr, line)
  if not vim.api.nvim_buf_is_valid(bufnr) then
    return
  end
  
  local ok, err = pcall(function()
    vim.api.nvim_buf_call(bufnr, function()
      local lnum = line + 1
      vim.fn.cursor(lnum, 1)
      -- Open outer folds
      vim.cmd('normal! zv')
      -- Delete fold at this line
      if vim.fn.foldclosed(lnum) ~= -1 then
        vim.cmd('normal! zd')
      end
    end)
  end)
  
  if not ok then
    vim.notify('Failed to delete fold: ' .. tostring(err), vim.log.levels.DEBUG)
  end
  
  -- Clear summary
  if M.fold_summaries[bufnr] then
    M.fold_summaries[bufnr][line] = nil
  end
end

---Clean up fold data for a buffer
---@param bufnr number Buffer number
function M.cleanup(bufnr)
  if M.fold_summaries[bufnr] then
    M.fold_summaries[bufnr] = nil
  end
  
  vim.api.nvim_buf_clear_namespace(bufnr, NS_FOLD, 0, -1)
end

return M
