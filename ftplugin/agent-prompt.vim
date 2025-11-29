" ftplugin/agent-prompt.vim
setlocal nonumber
setlocal norelativenumber
setlocal signcolumn=no
setlocal wrap
syntax enable

" Map Ctrl+C to cancel
nnoremap <buffer> <silent> <C-c> :AgentCancel<CR>
inoremap <buffer> <silent> <C-c> <Esc>:AgentCancel<CR>

" Load history functionality
lua dofile(vim.fn.expand('<sfile>:p:h') .. '/agent-prompt-history.lua')

" Map history navigation (normal mode only)
nnoremap <buffer> <silent> <C-p> :lua _G.AgentHistoryPrevVim()<CR>
nnoremap <buffer> <silent> <C-n> :lua _G.AgentHistoryNextVim()<CR>

" Set up blink.cmp for this buffer if available
lua if package.loaded['blink.cmp'] then
\   require('agent_nvim.blink.config').setup_buffer()
\ else
\   vim.opt_local.completefunc = 'AgentComplete'
\ end

" Set up Enter key mappings with history support
inoremap <buffer> <CR> <Esc><Cmd>AgentSubmit<CR>
noremap <buffer> <CR> <Cmd>AgentSubmit<CR>

" Highlight file references as user types
augroup AgentPromptHighlight
    autocmd!
    autocmd TextChanged <buffer> silent! call AgentHighlightPrompt()
    autocmd TextChangedI <buffer> silent! call AgentHighlightPrompt()
augroup END
