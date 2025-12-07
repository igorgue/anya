-- Auto-initialize fidget integration if available
local ok, fidget = pcall(require, "anya.fidget")
if ok then
  fidget:init()
end
