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

function M.highlight_slash_commands(bufnr)
    if not vim.api.nvim_buf_is_valid(bufnr) then
        return
    end
    
    local lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)
    
    -- Create namespace for slash command highlights
    local ns = vim.api.nvim_create_namespace("agent_slash_commands")
    vim.api.nvim_buf_clear_namespace(bufnr, ns, 0, -1)
    
    for line_num, line in ipairs(lines) do
        -- Match slash commands at start of line: /command
        local start_pos, end_pos = string.match(line, "^/([a-zA-Z]+)")
        if start_pos then
            local full_start = 0
            local full_end = string.len("/" .. start_pos)
            vim.api.nvim_buf_add_highlight(bufnr, ns, "Special", line_num - 1, full_start, full_end)
        end
        
        -- Match slash commands after whitespace:  /command
        local ws_start, ws_end, ws_command = string.match(line, "(%s+)/([a-zA-Z]+)")
        if ws_command then
            local full_start = ws_end - 1  -- Position of the slash
            local full_end = ws_end - 1 + string.len("/" .. ws_command)
            vim.api.nvim_buf_add_highlight(bufnr, ns, "Special", line_num - 1, full_start, full_end)
        end
    end
end

function M.highlight_all(bufnr)
    M.highlight_file_refs(bufnr)
    M.highlight_slash_commands(bufnr)
end

function M.set_placeholder(text, highlight_group)
    -- Store placeholder info in global state for status display
    _G.agent_placeholder = {
        text = text,
        highlight_group = highlight_group or "Normal"
    }
end

return M
