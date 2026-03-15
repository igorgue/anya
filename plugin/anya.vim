" Anya - AI Assistant for Neovim
" Maintainer: Igor
" Version: see ./rplugin/python3/anya/__init__.py

if exists('g:loaded_anya')
  finish
endif

let s:plugin_root = fnamemodify(resolve(expand('<sfile>:p')), ':h:h')

function! s:get_python_host_prog() abort
  if exists('g:python3_host_prog') && executable(g:python3_host_prog)
    return g:python3_host_prog
  endif

  try
    python3 import sys, vim; vim.vars['_anya_detected_python3_host_prog'] = sys.executable
  catch
  endtry

  if exists('g:_anya_detected_python3_host_prog')
    let l:detected = g:_anya_detected_python3_host_prog
    unlet g:_anya_detected_python3_host_prog
    if executable(l:detected)
      return l:detected
    endif
  endif

  return exepath('python3')
endfunction

function! s:on_install_exit(python, job, code, event) abort
  if a:code != 0
    echohl WarningMsg
    echom 'Anya: failed to install into ' .. a:python
    echohl None
    return
  endif

  silent! UpdateRemotePlugins
endfunction

function! s:install_anya_async(python) abort
  call jobstart([a:python, '-m', 'pip', 'install', '-e', s:plugin_root], {
        \ 'stdout_buffered': v:true,
        \ 'stderr_buffered': v:true,
        \ 'on_exit': function('s:on_install_exit', [a:python]),
        \ })
endfunction

function! s:on_import_check_exit(python, job, code, event) abort
  if a:code == 0
    return
  endif

  call s:install_anya_async(a:python)
endfunction

function! s:ensure_anya_installed() abort
  let l:python = s:get_python_host_prog()
  if empty(l:python)
    return
  endif

  call jobstart([l:python, '-c', 'import anya'], {
        \ 'stdout_buffered': v:true,
        \ 'stderr_buffered': v:true,
        \ 'on_exit': function('s:on_import_check_exit', [l:python]),
        \ })
endfunction

call s:ensure_anya_installed()

let g:loaded_anya = 1

" Define completion function for :Anya command
function! AnyaComplete(ArgLead, CmdLine, CursorPos)
  " Get the command line without the initial ':Anya '
  let cmdline = a:CmdLine
  if cmdline =~# '^:Anya\>'
    let cmdline = substitute(cmdline, '^:Anya\s*', '', '')
  endif

  " Split into parts
  let parts = split(cmdline)
  let subcommands = ['daemon', 'help', 'open', 'close', 'toggle', 'send', 'do', 'tab', 'pane', 'history', 'cancel', 'system-prompt']

  " If no arguments yet or we're completing the first subcommand
  if len(parts) <= 1 || (len(parts) == 1 && a:ArgLead != '')
    return filter(copy(subcommands), 'v:val =~# "^" . a:ArgLead')
  endif

  " Complete subcommand arguments
  if len(parts) >= 1
    let first_cmd = parts[0]

    " Complete actions for 'daemon' subcommand
    if first_cmd ==# 'daemon'
      let daemon_cmds = ['status', 'start', 'stop', 'restart']
      if len(parts) == 1 || (len(parts) == 2 && a:ArgLead != '')
        return filter(copy(daemon_cmds), 'v:val =~# "^" . a:ArgLead')
      elseif len(parts) == 2 && a:ArgLead == ''
        return daemon_cmds
      endif
    endif

    " Complete directions for 'pane' subcommand
    if first_cmd ==# 'pane' && len(parts) == 2 && a:ArgLead != ''
      return filter(['right', 'left'], 'v:val =~# "^" . a:ArgLead')
    elseif first_cmd ==# 'pane' && len(parts) == 2 && a:ArgLead == ''
      return ['right', 'left']
    endif
  endif

  return []
endfunction
