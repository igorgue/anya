" ftplugin/agent-prompt.vim
setlocal nonumber
setlocal norelativenumber
setlocal signcolumn=no
setlocal wrap

" Map Enter to submit (silent to avoid flashing)
nnoremap <buffer> <silent> <CR> :AgentSubmit<CR>
inoremap <buffer> <silent> <CR> <Esc>:AgentSubmit<CR>

" Set completion function
setlocal completefunc=AgentComplete
