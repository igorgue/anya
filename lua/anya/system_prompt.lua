-- Display the Code agent's system prompt in a scratch buffer

local M = {}

--- Fetch the system prompt from the daemon and display it
function M.show()
  vim.fn.AnyaGetSystemPrompt(vim.fn.getcwd())
end

--- Fallback: open the prompt in a simple scratch buffer
---@param lines string[]
local function open_simple_scratch(lines)
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_name(buf, "Anya System Prompt")
  vim.bo[buf].buftype = "nofile"
  vim.bo[buf].bufhidden = "wipe"
  vim.bo[buf].swapfile = false
  vim.bo[buf].filetype = "anya-system-prompt"
  vim.bo[buf].modifiable = true
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
  vim.bo[buf].modifiable = false
  vim.bo[buf].readonly = true

  vim.cmd("vsplit")
  vim.api.nvim_win_set_buf(0, buf)
end

--- Display the prompt in a scratch buffer
---@param prompt string The system prompt text
function M.display(prompt)
  local lines = vim.split(prompt, "\n", { plain = true })
  local snacks_ok, Snacks = pcall(require, "snacks")

  if snacks_ok and Snacks.scratch and Snacks.scratch.open then
    local win = Snacks.scratch.open({
      name = "Anya System Prompt",
      ft = "anya-system-prompt",
      icon = "󰘦",
      autowrite = false,
      win = {
        style = "scratch",
        wo = { winhighlight = "NormalFloat:Normal" },
        bo = {
          buftype = "nofile",
          bufhidden = "wipe",
          swapfile = false,
          filetype = "anya-system-prompt",
        },
      },
    })

    if win and win.buf then
      vim.bo[win.buf].modifiable = true
      vim.api.nvim_buf_set_lines(win.buf, 0, -1, false, lines)
      vim.bo[win.buf].modifiable = false
      vim.bo[win.buf].readonly = true
    end

    return
  end

  open_simple_scratch(lines)
end

return M
