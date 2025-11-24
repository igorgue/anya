" Enable line wrapping for agent content buffer
setlocal wrap
setlocal linebreak
setlocal nolist

" Set up folding for tool calls
setlocal foldmethod=manual
setlocal foldtext=v:lua.require('agent_nvim.folds').fold_text()
