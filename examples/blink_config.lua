-- Example blink.cmp configuration for agent.nvim
-- Add this to your Neovim config (e.g., init.lua)

return {
  "saghen/blink.cmp",
  lazy = false, -- or lazy = true with appropriate keys
  version = '*', -- Use latest stable version
  opts = {
    -- Your regular blink.cmp configuration
    keymap = {
      preset = 'default',
      -- Custom mappings can be added here
    },

    -- Sources configuration
    sources = {
      default = { 'lsp', 'path' },
      providers = {
        -- agent.nvim file completions (@ mentions)
        agent_files = {
          name = 'Agent Files',
          module = 'agent_nvim.blink.files',
          enabled = function()
            return vim.bo.filetype == 'agent-prompt'
          end,
        },

        -- agent.nvim command completions (/ commands)
        agent_commands = {
          name = 'Agent Commands',
          module = 'agent_nvim.blink.commands',
          enabled = function()
            return vim.bo.filetype == 'agent-prompt'
          end,
        },
      },
    },
  },

  -- Optional: Set up agent.nvim integration
  config = function(_, opts)
    require('blink.cmp').setup(opts)

    -- Set up agent.nvim blink integration (optional, since ftplugin handles it)
    require('agent_nvim.blink.config').setup()
  end,
}