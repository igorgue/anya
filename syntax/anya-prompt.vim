" Syntax highlighting for anya prompt buffer

if exists("b:current_syntax")
    finish
endif

" Clear any existing syntax to avoid conflicts
syntax clear

" Highlight file references like @filename or @path/to/file
syntax match AnyaFileRef "@[a-zA-Z0-9_.~/-]\+"
highlight link AnyaFileRef Constant

" Highlight slash commands like /help, /clear (at start or after space)
syntax match AnyaSlashCommand "\%(\%^\|\s\)\@<=/[a-zA-Z]\+\%($\|\s\)\@="
highlight link AnyaSlashCommand Special

let b:current_syntax = "anya-prompt"
