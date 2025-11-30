local M = {}

function M.create_fold(bufnr, start_line, end_line, fold_text)
    if not vim.api.nvim_buf_is_valid(bufnr) then
        return false
    end

    local line_count = vim.api.nvim_buf_line_count(bufnr)
    if start_line < 1 or end_line > line_count or start_line > end_line then
        return false
    end

    -- Find a window displaying this buffer and use nvim_win_call
    -- nvim_win_call executes in window context without visual switching
    for _, win in ipairs(vim.api.nvim_list_wins()) do
        if vim.api.nvim_win_get_buf(win) == bufnr then
            local ok = pcall(function()
                vim.api.nvim_win_call(win, function()
                    vim.cmd(string.format("%d,%dfold", start_line, end_line))
                end)
            end)
            return ok
        end
    end

    return false
end

return M
