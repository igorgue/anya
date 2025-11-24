" agent.vim

" Define AgentInstall command in Vimscript to bootstrap the installation
" without relying on the remote plugin being loaded.

command! AgentInstall call s:AgentInstall()

function! s:AgentInstall()
    " Get the plugin root directory from the current script location
    let l:plugin_root = expand('<sfile>:p:h:h')
    let l:script = l:plugin_root . '/scripts/install.py'
    
    " Verify the script exists
    if !filereadable(l:script)
        echohl ErrorMsg
        echomsg "Installation script not found: " . l:script
        echohl None
        return
    endif
    
    let l:cmd = 'python3 ' . shellescape(l:script)
    
    echo "Running installation script from: " . l:plugin_root
    " Run in a terminal buffer if possible, or just system()
    if has('nvim')
        split
        enew
        call termopen(l:cmd)
        startinsert
    else
        echo system(l:cmd)
    endif
endfunction
