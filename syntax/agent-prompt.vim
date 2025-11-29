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
" Use negative lookbehind to NOT match if preceded by @ or path characters
syntax match AgentSlashCommand "\%(\%^\|[^a-zA-Z0-9_./@-]\)\@<=/[a-z]\+"
highlight link AgentSlashCommand Special

let b:current_syntax = "agent-prompt"
