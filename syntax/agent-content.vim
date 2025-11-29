" Syntax highlighting for agent content buffer

" Mark the buffer as using syntax highlighting
if exists("b:current_syntax")
    finish
endif

" Clear any existing syntax to avoid conflicts
syntax clear

" Highlight file references like @filename or @path/to/file
" Define this LAST so it takes priority (later definitions win in Vim)
" The file ref pattern includes slashes, so it will match the whole path
syntax match AgentFileRef "@[a-zA-Z0-9_./-]\+"
highlight link AgentFileRef Directory

" Highlight slash commands like /help, /clear, /cancel
" Use negative lookbehind to NOT match if preceded by @ or path characters
syntax match AgentSlashCommand "\%(\%^\|[^a-zA-Z0-9_./@-]\)\@<=/[a-z]\+"
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
