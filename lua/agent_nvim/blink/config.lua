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
        -- Enter key mappings are handled in ftplugin/agent-prompt.vim with history support
      },
    })
  end
end

function M.setup_buffer()
  -- Set up blink.cmp for the current buffer
  if package.loaded['blink.cmp'] then
    -- Enter key mappings are now handled in ftplugin/agent-prompt.vim with history support
    -- This function now only sets up blink.cmp completion
  end
end

return M