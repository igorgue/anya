-- lua/agent_nvim/init.lua
-- Initialize agent.nvim module and set up global functions

local highlight = require("agent_nvim.highlight")

-- AgentSetPlaceholder is now provided by ftplugin/agent-prompt.lua
-- This just ensures the module loads successfully

return {
    highlight = highlight,
}
