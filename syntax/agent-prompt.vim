" syntax/agent-prompt.vim
if exists("b:current_syntax")
  finish
endif

syntax match AgentSlashCommand "^/[a-zA-Z0-9_-]\+"
highlight default link AgentSlashCommand Special

syntax match AgentVariable "@[a-zA-Z0-9_./-]\+"
highlight default link AgentVariable Identifier

let b:current_syntax = "agent-prompt"
