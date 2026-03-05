-- Display the Code agent's system prompt in a snacks scratch buffer

local M = {}

--- Fetch the system prompt from the daemon and display it
function M.show()
  -- Call Python function which handles daemon communication
  vim.fn.AnyaGetSystemPrompt(vim.fn.getcwd())
end

--- Display the prompt in a snacks scratch buffer
---@param prompt string The system prompt text
function M.display(prompt)
  local ok, snacks = pcall(require, "snacks")
  if not ok then
    vim.notify("[Anya] snacks.nvim is required to display the system prompt.", vim.log.levels.ERROR)
    return
  end
  
  -- Open a scratch buffer with the prompt
  snacks.scratch({
    name = "Anya System Prompt",
    ft = "anya-system-prompt",
    content = prompt,
    win = {
      style = "float",
    },
    bo = {
      readonly = true,
      modifiable = false,
      bufhidden = "wipe",
      buftype = "nofile",
    },
  })
end

return M
