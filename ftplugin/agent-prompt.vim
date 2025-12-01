" ftplugin/agent-prompt.vim
setlocal nonumber
setlocal norelativenumber
setlocal signcolumn=no
setlocal wrap
" Remove standard scrolloff - we'll handle it with custom logic below
syntax enable

" Map Ctrl+C to cancel
nnoremap <buffer> <silent> <C-c> :AgentCancel<CR>
inoremap <buffer> <silent> <C-c> <Esc>:AgentCancel<CR>

" Toolbar keymaps (localleader defaults to \ if not set)
nnoremap <buffer> <silent> <localleader>a :lua require('agent_nvim.toolbar').toggle_agent()<CR>
inoremap <buffer> <silent> <localleader>a <Esc>:lua require('agent_nvim.toolbar').toggle_agent()<CR>a
nnoremap <buffer> <silent> <localleader>y :lua require('agent_nvim.toolbar').toggle_mode()<CR>
inoremap <buffer> <silent> <localleader>y <Esc>:lua require('agent_nvim.toolbar').toggle_mode()<CR>a
nnoremap <buffer> <silent> <localleader>A :lua require('agent_nvim.toolbar').pick_agent()<CR>
inoremap <buffer> <silent> <localleader>A <Esc>:lua require('agent_nvim.toolbar').pick_agent()<CR>a

" Alternative keymaps using Ctrl (in case localleader is not configured)
nnoremap <buffer> <silent> <C-g>a :lua require('agent_nvim.toolbar').toggle_agent()<CR>
inoremap <buffer> <silent> <C-g>a <Esc>:lua require('agent_nvim.toolbar').toggle_agent()<CR>a
nnoremap <buffer> <silent> <C-g>y :lua require('agent_nvim.toolbar').toggle_mode()<CR>
inoremap <buffer> <silent> <C-g>y <Esc>:lua require('agent_nvim.toolbar').toggle_mode()<CR>a
nnoremap <buffer> <silent> <C-g>A :lua require('agent_nvim.toolbar').pick_agent()<CR>
inoremap <buffer> <silent> <C-g>A <Esc>:lua require('agent_nvim.toolbar').pick_agent()<CR>a

" Load history functionality
lua << EOF
  local history_script = vim.fn.expand('<sfile>:p:h') .. '/agent-prompt-history.lua'
  local success, err = pcall(function() dofile(history_script) end)

  if not success then
    vim.notify('Failed to load agent history: ' .. (err or 'unknown error'), vim.log.levels.ERROR)
  -- else
  --   vim.notify('Agent history loaded successfully', vim.log.levels.DEBUG)
  end
EOF

" Map history navigation (normal mode only)
nnoremap <buffer> <silent> <C-p> :lua _G.AgentHistoryPrevVim()<CR>
nnoremap <buffer> <silent> <C-n> :lua _G.AgentHistoryNextVim()<CR>

" Set up blink.cmp for this buffer if available
lua if package.loaded['blink.cmp'] then
\   require('agent_nvim.blink.config').setup_buffer()
\ else
\   vim.opt_local.completefunc = 'AgentComplete'
\ end

" Set up Enter key mappings with history support
inoremap <buffer> <CR> <Esc><Cmd>AgentSubmit<CR>
noremap <buffer> <CR> <Cmd>AgentSubmit<CR>

" Highlight file references as user types
augroup AgentPromptHighlight
    autocmd!
    autocmd TextChanged <buffer> silent! call AgentHighlightPrompt()
    autocmd TextChangedI <buffer> silent! call AgentHighlightPrompt()
augroup END

" Maintain prompt window height when terminal is resized
augroup AgentPromptResize
    autocmd!
    autocmd VimResized <buffer> silent! resize 5
augroup END

" Simple bottom scrolloff to keep cursor above toolbar
augroup AgentPromptBottomScrolloff
    autocmd!
    autocmd CursorMoved,CursorMovedI <buffer> call s:BottomScrolloff()
augroup END

" Initialize with no scrolloff
setlocal scrolloff=0

" Function to handle bottom-only scrolloff
function! s:BottomScrolloff()
    let total_lines = line('$')
    let current_line = line('.')
    let distance_to_bottom = total_lines - current_line
    
    " Apply scrolloff only when within 1 lines of bottom (reserve space for toolbar)
    if distance_to_bottom < 1
        setlocal scrolloff=1
    else
        setlocal scrolloff=0
    endif
endfunction
