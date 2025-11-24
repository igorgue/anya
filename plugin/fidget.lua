-- Auto-initialize fidget integration if available
local ok, fidget = pcall(require, "agent.fidget")
if ok then
	fidget:init()
end
