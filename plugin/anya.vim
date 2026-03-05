" Anya - AI Assistant for Neovim
" Maintainer: Igor
" Version: see ./rplugin/python3/anya/__init__.py

if exists('g:loaded_anya')
  finish
endif

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
