" Syntax highlighting for anya prompt buffer
" Loads markdown syntax and adds custom highlighting on top

" Load markdown syntax if available
if !exists("b:current_syntax")
  runtime! syntax/markdown.vim
  unlet b:current_syntax
endif

" Clear any existing syntax to avoid conflicts
syntax clear

" Load markdown syntax
runtime! syntax/markdown.vim

" Highlight file references like @filename or @path/to/file
syntax match AnyaFileRef "@[a-zA-Z0-9_.~/\-\\ ]\+"
highlight link AnyaFileRef Constant

" Highlight conversation ID references like #abc123 (no space after #)
syntax match AnyaConvRef "#[a-zA-Z0-9_-]\+"
highlight link AnyaConvRef Function

" Highlight slash commands like /help, /clear (at start or after space)
syntax match AnyaSlashCommand "\%(\%^\|\s\)\@<=/[a-zA-Z]\+\%($\|\s\)\@="
highlight link AnyaSlashCommand Special

let b:current_syntax = "anya-prompt"
