-- Debug script to check Anya window configuration
-- Run with :luafile debug_wins.lua when Anya is open

local function dump_win(win)
  if not vim.api.nvim_win_is_valid(win) then
    return nil
  end
  
  local buf = vim.api.nvim_win_get_buf(win)
  local config = vim.api.nvim_win_get_config(win)
  local ft = vim.api.nvim_buf_get_option(buf, "filetype")
  
  return {
    win = win,
    ft = ft,
    focusable = config.focusable,
    relative = config.relative,
    row = config.row,
    col = config.col,
    width = config.width,
    height = config.height,
    zindex = config.zindex,
  }
end

print("=== Anya Windows ===")
for _, win in ipairs(vim.api.nvim_tabpage_list_wins(0)) do
  local info = dump_win(win)
  if info and (info.ft:match("anya") or vim.w[win].anya_layout) then
    print(vim.inspect(info))
  end
end

print("\n=== Try wincmd k from current window ===")
local current = vim.api.nvim_get_current_win()
print("Current win:", current)
print("Current ft:", vim.api.nvim_buf_get_option(0, "filetype"))

-- Try the command
vim.cmd("wincmd k")
local after = vim.api.nvim_get_current_win()
print("After wincmd k:", after)
print("Changed:", current ~= after)
