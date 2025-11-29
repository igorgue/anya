" Syntax highlighting for agent content buffer

" Mark the buffer as using syntax highlighting
if exists("b:current_syntax")
    finish
endif

" Highlight slash commands like /help, /clear, /cancel
syntax match AgentSlashCommand "/[a-z]\+" display
highlight link AgentSlashCommand Special

" Highlight user prompt sections
" Match text that follows # Username pattern
" This is a simplified approach - we'll rely on highlighting during insertion

" Define syntax for the header (# Username)
syntax match AgentUserHeader "^#.*$" contains=AgentUsername
syntax match AgentUsername "^\# .*$" contained

" User prompt text - this will be highlighted in the buffer via Python
" We define the highlight group here so it can be referenced
highlight link AgentUsername CursorLineNr

let b:current_syntax = "agent-content"
