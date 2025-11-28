local M = {}

function M.create_fold(bufnr, start_line, end_line, fold_text)
    if not vim.api.nvim_buf_is_valid(bufnr) then
        return false
    end

    local line_count = vim.api.nvim_buf_line_count(bufnr)
    if start_line < 1 or end_line > line_count or start_line > end_line then
        return false
    end

    for _, win in ipairs(vim.api.nvim_list_wins()) do
        if vim.api.nvim_win_get_buf(win) == bufnr then
            local saved_view = vim.fn.winsaveview()
            vim.api.nvim_set_current_win(win)
            
            vim.cmd(string.format("%d,%dfold", start_line, end_line))
            
            vim.fn.winrestview(saved_view)
            return true
        end
    end

    return false
end

return M
