" Syntax for anya-chat buffers
" Note: Marker concealment is handled by extmarks in lua/anya/text.lua

if exists("b:current_syntax")
    finish
endif

" Highlight file references like @filename or @path/to/file
syntax match AnyaFileRef "@[a-zA-Z0-9_.~/-]\+"
highlight link AnyaFileRef Constant

" Highlight slash commands like /help, /clear (at start or after space)
syntax match AnyaSlashCommand "\%(\%^\|\s\)\@<=/[a-zA-Z]\+\%($\|\s\)\@="
highlight link AnyaSlashCommand Special

" Winbar highlight group
highlight link AnyaWinBar Comment

let b:current_syntax = "anya-chat"
