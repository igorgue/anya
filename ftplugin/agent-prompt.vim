" ftplugin/agent-prompt.vim
setlocal nonumber
setlocal norelativenumber
setlocal signcolumn=no
setlocal wrap
syntax enable

" Map Enter to submit (silent to avoid flashing)
nnoremap <buffer> <silent> <CR> :AgentSubmit<CR>
inoremap <buffer> <silent> <CR> <Esc>:AgentSubmit<CR>

" Map Ctrl+C to cancel
nnoremap <buffer> <silent> <C-c> :AgentCancel<CR>
inoremap <buffer> <silent> <C-c> <Esc>:AgentCancel<CR>

" Set completion function
setlocal completefunc=AgentComplete

" Highlight file references as user types
augroup AgentPromptHighlight
    autocmd!
    autocmd TextChanged <buffer> silent! call AgentHighlightPrompt()
    autocmd TextChangedI <buffer> silent! call AgentHighlightPrompt()
augroup END
