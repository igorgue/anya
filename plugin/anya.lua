-- Anya - AI Assistant for Neovim
-- Bootstrap: stub command + completion, available even before Python rplugin loads.
-- When the Python rplugin loads, it overwrites the :Anya command via command!
-- and AnyaComplete resolves through the rplugin's remote function registry.

if vim.g.loaded_anya then
  return
end
vim.g.loaded_anya = 1

local subcommands = {
  "daemon",
  "help",
  "open",
  "close",
  "toggle",
  "send",
  "do",
  "tab",
  "pane",
  "history",
  "cancel",
  "system-prompt",
  "copilot",
  "telegram",
}

local sub_opts = {
  daemon = { "status", "start", "stop", "restart" },
  pane = { "right", "left" },
  copilot = { "login", "logout", "status", "models" },
  telegram = { "pair" },
}

local function filter_prefix(items, prefix)
  prefix = prefix or ""
  return vim.tbl_filter(function(item)
    return prefix == "" or vim.startswith(item, prefix)
  end, items)
end

local function anya_complete(lead, line, pos)
  local stripped = line:gsub("^:?Anya%s*", "")
  local ends_with_space = stripped:match("%s$") ~= nil
  local parts = stripped == "" and {} or vim.split(vim.trim(stripped), "%s+", { plain = false, trimempty = true })

  if #parts == 0 then
    return filter_prefix(subcommands, lead)
  end

  if #parts == 1 and not ends_with_space then
    return filter_prefix(subcommands, lead)
  end

  local subcommand = parts[1]
  local opts = sub_opts[subcommand]
  if not opts then
    return {}
  end

  if #parts == 1 and ends_with_space then
    return opts
  end

  if #parts == 2 and not ends_with_space then
    return filter_prefix(opts, lead)
  end

  return {}
end

-- Define AnyaComplete as a Vimscript function so the rplugin's
-- complete="customlist,AnyaComplete" can resolve it even before
-- the Python module loads.
vim.api.nvim_exec2(
  [[
function! AnyaComplete(A, L, P)
  return v:lua.__anya_complete(a:A, a:L, a:P)
endfunction
]],
  {}
)

-- Expose for v:lua bridge
_G.__anya_complete = anya_complete

-- Stub :Anya command with completion. Overwritten by the Python rplugin
-- when it loads (via command! force-replace).
vim.api.nvim_create_user_command("Anya", function(opts)
  vim.notify("Anya is loading...", vim.log.levels.INFO)
end, {
  nargs = "*",
  complete = "customlist,AnyaComplete",
})
