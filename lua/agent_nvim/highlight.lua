-- lua/agent_nvim/highlight.lua
-- Shared highlighting functions for agent buffers

local M = {}

function M.highlight_file_refs(bufnr)
    if not vim.api.nvim_buf_is_valid(bufnr) then
        return
    end
    
    local lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)
    
    -- Create namespace for our highlights
    local ns = vim.api.nvim_create_namespace("agent_file_refs")
    vim.api.nvim_buf_clear_namespace(bufnr, ns, 0, -1)
    
    for line_num, line in ipairs(lines) do
        -- Find all file references like @path/to/file
        local pos = 1
        while pos <= #line do
            local start_pos, end_pos = string.find(line, "@[a-zA-Z0-9_./-]+/[a-zA-Z0-9_./-]*", pos)
            if not start_pos then break end
            vim.api.nvim_buf_add_highlight(bufnr, ns, "Directory", line_num - 1, start_pos - 1, end_pos)
            pos = end_pos + 1
        end
    end
end

function M.set_placeholder(text, highlight_group)
    -- Store placeholder info in global state for status display
    _G.agent_placeholder = {
        text = text,
        highlight_group = highlight_group or "Normal"
    }
end

return M
