# Blink.cmp Integration for Agent.nvim

This document describes how to set up and use blink.cmp with agent.nvim for improved completion functionality.

## Overview

Agent.nvim includes custom blink.cmp sources that provide:
- File completions for `@` mentions
- Slash command completions (`/clear`, `/cancel`, `/help`)
- Smart Enter key behavior that accepts completions when the menu is visible, otherwise submits the prompt

## Setup

### Prerequisites

1. Install [blink.cmp](https://github.com/Saghen/blink.cmp) using your preferred plugin manager
2. Configure blink.cmp in your Neovim configuration

### Basic Configuration

Add the following to your Neovim configuration:

```lua
-- Initialize blink.cmp with agent.nvim integration
require('blink.cmp').setup({
  -- Your other blink.cmp configuration

  -- Enable agent.nvim sources
  sources = {
    providers = {
      agent_files = {
        name = 'Agent Files',
        module = 'agent_nvim.blink.files',
        enabled = function()
          return vim.bo.filetype == 'agent-prompt'
        end,
      },
      agent_commands = {
        name = 'Agent Commands',
        module = 'agent_nvim.blink.commands',
        enabled = function()
          return vim.bo.filetype == 'agent-prompt'
        end,
      },
    },
  },

  -- Configure agent-prompt filetype
  filetype_config = {
    agent_prompt = {
      sources = { 'agent_commands', 'agent_files' }
    }
  },

  -- Handle Enter key correctly for agent prompts
  keymap = {
    preset = 'default',
    ['<CR>'] = {
      function(cmp)
        if cmp.visible() then
          cmp.select_and_accept()
          return true
        end
        -- Only run AgentSubmit in agent-prompt buffers
        if vim.bo.filetype == 'agent-prompt' then
          vim.api.nvim_feedkeys(
            vim.api.nvim_replace_termcodes('<Esc>:AgentSubmit<CR>', true, true, true),
            'n', true
          )
        end
        return true
      end
    },
  },
})
```

### Automatic Setup

Agent.nvim includes automatic setup logic in `ftplugin/agent-prompt.vim`. The plugin will:

1. Detect if blink.cmp is available
2. If available, configure agent-specific sources and keybindings
3. If not available, fall back to the traditional `completefunc` approach

## Features

### File Completions (`@` mentions)

- Type `@` followed by part of a filename to see completions
- Uses the same recursive file search as the original completion system
- Limited to 50 results to prevent overwhelming the UI
- Excludes `.git` directories

### Slash Command Completions

- Type `/` at the beginning of a line or after a space
- Shows available commands: `/clear`, `/cancel`, `/help`
- Includes descriptions for each command

### Smart Enter Key Behavior

- **When completion menu is visible**: Enter accepts the selected completion
- **When completion menu is not visible**: Enter submits the prompt to the agent
- This behavior is only active in `agent-prompt` buffers

## Troubleshooting

### Completions Not Working

1. Ensure blink.cmp is properly installed and configured
2. Check that the filetype is correctly set to `agent-prompt`
3. Verify that the agent.nvim Lua modules are in your runtimepath

### Enter Key Not Working

1. Make sure the keymapping is set up correctly in the buffer
2. Check that `AgentSubmit` command is available
3. Verify you're in an `agent-prompt` buffer

### Fallback Mode

If blink.cmp is not detected, agent.nvim will automatically fall back to:
- Traditional `completefunc` for file completions (`@` mentions)
- Standard Enter key mappings for prompt submission

This ensures compatibility with setups that don't use blink.cmp.

## Files

The blink.cmp integration consists of these files:

- `lua/agent_nvim/blink/files.lua` - File completion source for `@` mentions
- `lua/agent_nvim/blink/commands.lua` - Command completion source for `/` commands
- `lua/agent_nvim/blink/init.lua` - Async completion utilities and callback management
- `lua/agent_nvim/blink/config.lua` - Configuration and setup functions
- `ftplugin/agent-prompt.vim` - Buffer setup with blink.cmp detection and fallback