-- Anya - AI Assistant for Neovim
-- Bootstrap logic lives in the lazy.nvim spec's init() callback,
-- which always runs at startup even for lazy plugins.
-- This file is sourced when the lazy trigger fires; nothing to do here.

if vim.g.loaded_anya then
  return
end
vim.g.loaded_anya = 1
