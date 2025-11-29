" Enable line wrapping for agent content buffer
setlocal wrap
setlocal linebreak
setlocal nolist

" Enable manual folding for tool calls
setlocal foldmethod=manual
setlocal foldlevel=99

" Initialize autoscroll state
if !exists('b:agent_autoscroll_enabled')
    let b:agent_autoscroll_enabled = 1
endif

" Handler for scroll events - detects when user scrolls
function! AgentContentOnScroll() abort
    let line_count = line('$')
    let cursor_line = line('.')
    
    " Check if cursor is at the last line (or very close to it)
    " Allow a small tolerance for wrapped lines
    if cursor_line >= line_count - 1
        " User is at the bottom - enable autoscroll
        let b:agent_autoscroll_enabled = 1
    else
        " User scrolled up - disable autoscroll
        let b:agent_autoscroll_enabled = 0
    endif
endfunction

" Set up event handling for scroll and cursor movement
augroup AgentContentScroll
    autocmd!
    " WinScrolled detects scrolling with mouse or scroll wheel
    if exists('##WinScrolled')
        autocmd WinScrolled <buffer> call AgentContentOnScroll()
    endif
    " CursorMoved detects movement with arrow keys, hjkl, page up/down, etc
    autocmd CursorMoved <buffer> call AgentContentOnScroll()
    autocmd CursorMovedI <buffer> call AgentContentOnScroll()
augroup END


