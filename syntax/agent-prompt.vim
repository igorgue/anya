" Syntax highlighting for agent prompt buffer

if exists("b:current_syntax")
    finish
endif

" Highlight slash commands like /help, /clear, /cancel
syntax match AgentSlashCommand "^/[a-z]\+" display
highlight link AgentSlashCommand Special

" Highlight file references like @filename or @path/to/file
syntax match AgentFileRef "@[a-zA-Z0-9_./-]\+" display
highlight link AgentFileRef Directory

let b:current_syntax = "agent-prompt"
