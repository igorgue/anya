" Syntax highlighting for agent prompt buffer

if exists("b:current_syntax")
    finish
endif

" Clear any existing syntax to avoid conflicts
syntax clear

" Highlight file references like @filename or @path/to/file
" Define this LAST so it takes priority (later definitions win in Vim)
syntax match AgentFileRef "@[a-zA-Z0-9_./-]\+"
highlight link AgentFileRef Directory

" Highlight slash commands like /help, /clear, /cancel
" Match slash only at start of line or preceded by whitespace
syntax match AgentSlashCommand "\%(\%^\|\s\)\@<=/[a-zA-Z]\+\%($\|\s\)"
highlight link AgentSlashCommand Special

let b:current_syntax = "agent-prompt"
