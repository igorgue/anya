" Syntax highlighting for agent content buffer
" Runs after markdown syntax (due to filetype=markdown.agent-content)

" No need to check b:current_syntax or load markdown manually

" SEARCH/REPLACE block highlighting (Aider-style)
" Match the markers
syntax match AgentSearchMarker "^<\{5,9} SEARCH>*\s*$"
syntax match AgentDividerMarker "^=\{5,9}\s*$"
syntax match AgentReplaceMarker "^>\{5,9} REPLACE\s*$"

" Link to highlight groups (will be overridden by Lua for dynamic highlighting)
highlight link AgentSearchMarker Comment
highlight link AgentDividerMarker Comment
highlight link AgentReplaceMarker Comment

" Highlight file references like @filename or @path/to/file
" Define this LAST so it takes priority (later definitions win in Vim)
" The file ref pattern includes slashes, so it will match the whole path
syntax match AgentFileRef "@[a-zA-Z0-9_./-]\+"
highlight link AgentFileRef Directory

" Highlight slash commands like /help, /clear, /cancel
" Match slash only at start of line or preceded by whitespace
syntax match AgentSlashCommand "\%(\%^\|\s\)\@<=/[a-zA-Z]\+\%($\|\s\)"
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
