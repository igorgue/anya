-- Example blink.cmp configuration for agent.nvim
-- Add this to your lazy.nvim config (e.g. lua/plugins/blink_cmp.lua)

return {
  "saghen/blink.cmp",
  opts = {
    -- Your regular blink.cmp configuration
    -- ...

    -- Sources configuration
    sources = {
      default = { "agent_files", "agent_commands" },
      providers = {
        -- agent.nvim file completions (@ mentions)
        agent_files = {
          name = "Agent Files",
          module = "agent_nvim.blink.files",
          enabled = function()
            return vim.bo.filetype == "agent-prompt"
          end,
        },

        -- agent.nvim command completions (/ commands)
        agent_commands = {
          name = "Agent Commands",
          module = "agent_nvim.blink.commands",
          enabled = function()
            return vim.bo.filetype == "agent-prompt"
          end,
        },
      },
    },
  },
}
