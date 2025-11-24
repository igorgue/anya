# Fidget Integration

agent.nvim supports optional integration with [fidget.nvim](https://github.com/j-hui/fidget.nvim) to show progress notifications for agent requests.

## Setup

If you have fidget.nvim installed, the integration is automatically enabled. No additional configuration is required.

The integration:
- Shows a progress spinner when an agent request starts
- Displays the model name (e.g., "🤖 Agent (gpt-4o)")
- Updates the status message on completion:
  - ✓ "Completed" on success
  - ⚠ "Error" on failure
  - 󰜺 "Cancelled" for cancelled requests

## How It Works

The plugin emits Neovim User autocommand events:
- `AgentRequestStarted` - when an agent request begins
- `AgentRequestFinished` - when an agent request completes (success/error)

The Lua module at `lua/agent/fidget.lua` listens for these events and manages fidget progress handles accordingly.

## Disabling

If you want to disable fidget integration while keeping fidget.nvim installed, you can prevent the fidget plugin from loading:

```lua
-- In your agent.nvim setup or init.lua
vim.g.agent_nvim_disable_fidget = 1
```

Then modify `plugin/fidget.lua` to check for this global variable before initializing.

## Manual Setup

If the automatic setup doesn't work, you can manually initialize the integration:

```lua
local fidget = require("agent.fidget")
fidget:init()
```
