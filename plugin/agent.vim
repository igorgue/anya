" agent.vim

" Define AgentInstall command in Vimscript to bootstrap the installation
" without relying on the remote plugin being loaded.

command! AgentInstall call s:AgentInstall()

function! s:AgentInstall()
    let l:script = '/home/igor/Code/agent.nvim/scripts/install.py'
    let l:cmd = 'python3 ' . shellescape(l:script)
    
    echo "Running installation script..."
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
