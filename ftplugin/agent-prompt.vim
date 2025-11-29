" ftplugin/agent-prompt.vim
setlocal nonumber
setlocal norelativenumber
setlocal signcolumn=no
setlocal wrap
syntax enable

" Map Ctrl+C to cancel
nnoremap <buffer> <silent> <C-c> :AgentCancel<CR>
inoremap <buffer> <silent> <C-c> <Esc>:AgentCancel<CR>

" Set up blink.cmp for this buffer if available
lua if package.loaded['blink.cmp'] then
\   require('agent_nvim.blink.config').setup_buffer()
\ else
\   vim.opt_local.completefunc = 'AgentComplete'
\   vim.keymap.set('n', '<CR>', '<Cmd>AgentSubmit<CR>', { buffer = true, desc = 'Submit agent prompt' })
\   vim.keymap.set('i', '<CR>', '<Esc><Cmd>AgentSubmit<CR>', { buffer = true, desc = 'Submit agent prompt' })
\ end

" Highlight file references as user types
augroup AgentPromptHighlight
    autocmd!
    autocmd TextChanged <buffer> silent! call AgentHighlightPrompt()
    autocmd TextChangedI <buffer> silent! call AgentHighlightPrompt()
augroup END
