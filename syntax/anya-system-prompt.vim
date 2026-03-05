" Syntax highlighting for anya system prompt buffer
" Uses markdown syntax since the system prompt is markdown

if exists("b:current_syntax")
    finish
endif

" Load markdown syntax
runtime! syntax/markdown.vim

let b:current_syntax = "anya-system-prompt"
