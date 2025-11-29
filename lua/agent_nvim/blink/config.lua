local M = {}

function M.setup()
  -- Configure blink.cmp sources globally
  if package.loaded['blink.cmp'] then
    local blink = require('blink.cmp')

    blink.setup({
      sources = {
        default = { 'lsp', 'path' },
        providers = {
          agent_files = {
            name = 'Agent Files',
            module = 'agent_nvim.blink.files',
            enabled = function()
              return vim.bo.filetype == 'agent-prompt'
            end,
          },
          agent_commands = {
            name = 'Agent Commands',
            module = 'agent_nvim.blink.commands',
            enabled = function()
              return vim.bo.filetype == 'agent-prompt'
            end,
          },
        },
      },

      keymap = {
        preset = 'default',
        ['<CR>'] = {
          function(cmp)
            -- If completion menu is visible, accept the selection
            if cmp.visible() then
              cmp.select_and_accept()
              return true
            end
            -- Otherwise, submit the prompt
            vim.api.nvim_feedkeys(vim.api.nvim_replace_termcodes('<Esc>:AgentSubmit<CR>', true, true, true), 'n', true)
            return true
          end
        },
      },
    })
  end
end

function M.setup_buffer()
  -- Set up blink.cmp for the current buffer
  if package.loaded['blink.cmp'] then
    -- Override Enter key behavior for this buffer
    vim.keymap.set('i', '<CR>', function()
      local cmp = require('blink.cmp')
      if cmp.visible() then
        cmp.select_and_accept()
      else
        vim.api.nvim_feedkeys(vim.api.nvim_replace_termcodes('<Esc>:AgentSubmit<CR>', true, true, true), 'n', true)
      end
    end, { buffer = true, desc = 'Accept completion or submit agent prompt' })

    vim.keymap.set('n', '<CR>', ':AgentSubmit<CR>', { buffer = true, desc = 'Submit agent prompt' })
  end
end

return M