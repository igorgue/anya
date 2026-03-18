-- Anya - AI Assistant for Neovim
-- Bootstrap: completion function + install guard.
-- The :Anya command is registered by the Python rplugin host.

if vim.g.loaded_anya then
  return
end
vim.g.loaded_anya = 1

-- Completion for :Anya (used by the Python rplugin command)
_G.AnyaComplete = function(arglead, cmdline, _)
  local stripped = cmdline:match("^:Anya%s*(.*)") or cmdline
  local parts = vim.split(stripped, "%s+")
  local subcommands = {
    "daemon", "help", "open", "close", "toggle",
    "send", "do", "tab", "pane", "history", "cancel", "system-prompt",
    "copilot",
  }

  if #parts <= 1 or (#parts == 1 and arglead ~= "") then
    return vim.tbl_filter(function(v) return v:find("^" .. vim.pesc(arglead)) end, subcommands)
  end

  local first = parts[1]
  if first == "daemon" then
    local opts = { "status", "start", "stop", "restart" }
    if #parts == 2 and arglead ~= "" then
      return vim.tbl_filter(function(v) return v:find("^" .. vim.pesc(arglead)) end, opts)
    elseif #parts == 2 and arglead == "" then
      return opts
    end
  elseif first == "pane" then
    local opts = { "right", "left" }
    if #parts == 2 and arglead ~= "" then
      return vim.tbl_filter(function(v) return v:find("^" .. vim.pesc(arglead)) end, opts)
    elseif #parts == 2 and arglead == "" then
      return opts
    end
  elseif first == "copilot" then
    local opts = { "login", "logout", "status", "models" }
    if #parts == 2 and arglead ~= "" then
      return vim.tbl_filter(function(v) return v:find("^" .. vim.pesc(arglead)) end, opts)
    elseif #parts == 2 and arglead == "" then
      return opts
    end
  end

  return {}
end

-- Ensure the Python package is available (sync check, silent)
do
  local python = vim.g.python3_host_prog or vim.fn.exepath("python3")
  if python ~= "" and vim.fn.executable(python) == 1 then
    local _ = vim.fn.system({ python, "-c", "import anya" })
    if vim.v.shell_error ~= 0 then
      vim.notify(
        "Anya: Python package not installed. Run: pip install -e "
          .. vim.fn.fnamemodify(
            vim.api.nvim_get_runtime_file("plugin/anya.lua", false)[1]
              or debug.getinfo(1).source:match("^@(.*/)"),
            ":h:h"
          ),
        vim.log.levels.ERROR
      )
    end
  end
end
