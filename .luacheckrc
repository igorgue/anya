-- Luacheck configuration for Neovim plugin

-- Recognize vim as a read/write global (Neovim's API)
globals = {
  "vim",
}

-- Ignore unused self warnings (common in OOP-style Lua)
self = false

-- Max line length (optional, adjust as needed)
max_line_length = 120

-- Ignore specific warnings
ignore = {
  "211/_.*", -- Unused local variable starting with underscore
  "212/_.*", -- Unused argument starting with underscore
}

exclude_files = {
  "standalone/.*",
}
