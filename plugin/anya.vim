" Anya - AI Assistant for Neovim
" Maintainer: Igor
" Version: see ./rplugin/python3/anya/__init__.py

if exists('g:loaded_anya')
  finish
endif

let g:loaded_anya = 1

" Command to open the conversation history picker
command! AnyaHistory lua require('anya.picker').open()
