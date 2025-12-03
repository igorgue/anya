" Enable line wrapping for agent content buffer
setlocal wrap
setlocal linebreak
setlocal nolist
let b:markdown_fenced_languages = ['diff']

" Map Ctrl+C to cancel
nnoremap <buffer> <silent> <C-c> :AgentCancel<CR>
inoremap <buffer> <silent> <C-c> <Esc>:AgentCancel<CR>

" Map G to go to end and re-enable autoscroll
nnoremap <buffer> <silent> G G:let b:agent_autoscroll_enabled = 1<CR>

" Map j to re-enable autoscroll when already at the last line
function! AgentContentSmartJ() abort
    if line('.') == line('$')
        let b:agent_autoscroll_enabled = 1
    endif
    normal! j
endfunction
nnoremap <buffer> <silent> j :call AgentContentSmartJ()<CR>

" Enable manual folding for tool calls
setlocal foldmethod=manual
setlocal foldlevel=99

" Initialize autoscroll state
if !exists('b:agent_autoscroll_enabled')
    let b:agent_autoscroll_enabled = 1
endif

" Track the last known line count to detect content growth vs user movement
if !exists('b:agent_last_line_count')
    let b:agent_last_line_count = line('$')
endif

" Handler for cursor movement - only disables autoscroll when user moves away from bottom
function! AgentContentOnCursorMoved() abort
    let line_count = line('$')
    let cursor_line = line('.')
    
    " If line count changed, content was added - don't change autoscroll state
    if line_count != b:agent_last_line_count
        let b:agent_last_line_count = line_count
        return
    endif
    
    " Line count is the same, so this is user cursor movement
    " Only disable autoscroll if user moved away from the last line
    if cursor_line < line_count
        let b:agent_autoscroll_enabled = 0
    endif
endfunction

" Set up event handling for cursor movement
augroup AgentContentScroll
    autocmd!
    " CursorMoved detects movement with arrow keys, hjkl, page up/down, etc
    autocmd CursorMoved <buffer> call AgentContentOnCursorMoved()
    autocmd CursorMovedI <buffer> call AgentContentOnCursorMoved()
augroup END

