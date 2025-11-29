-- lua/agent_nvim/init.lua
-- Initialize agent.nvim module and set up global functions

local highlight = require("agent_nvim.highlight")

-- AgentSetPlaceholder is now provided by ftplugin/agent-prompt.lua
-- This just ensures the module loads successfully

-- Function to highlight prompt buffer file references
local function highlight_prompt_buffer()
    -- Get the current plugin instance
    local plugin = vim.fn.rpcrequest(vim.fn.sockconnect('pipe', vim.fn.serverlist()[1] or ''), 'nvim_call_function', 'AgentHighlightPrompt', {})
end

return {
    highlight = highlight,
    highlight_prompt_buffer = function()
        pcall(function()
            if vim.fn.exists(':AgentHighlightPrompt') == 2 then
                vim.fn.AgentHighlightPrompt()
            end
        end)
    end,
}
