# Anya

A persistent Neovim AI assistant with multi-conversation support, built on OpenAI's Agents SDK.

## Features

- **Persistent Conversations**: Conversations survive Neovim restarts via SQLite storage
- **Multi-Layout Support**: Toggle between replace, pane, and tab layouts
- **Streaming Output**: Real-time LLM responses with animated text rendering
- **Prompt History**: Cycle through previous prompts with `<C-p>` and `<C-n>`
- **Context Awareness**: Automatic inclusion of selected code and `@file` references
- **Conversation Browser**: Browse and load previous conversations with `:Anya history`
- **Intelligent Code Tools**: File editing, searching, and execution with confirmation flow
- **OpenRouter & Custom Providers**: Drop-in support for any OpenAI-compatible API
- **MCP Tools**: Optional external tool access via Model Context Protocol

## Requirements

- Neovim 0.10+
- Python 3.13+
- An OpenAI API key (or compatible provider key)

## Optional Neovim plugins

These are not required, but enhance the experience:

| Plugin | Purpose |
|--------|---------|
| [blink.cmp](https://github.com/Saghen/blink.cmp) | Autocompletion for `@file` references and `/commands` in the prompt buffer |
| [snacks.nvim](https://github.com/folke/snacks.nvim) | Powers the `:Anya history` conversation picker |

## Installation

### lazy.nvim

```lua
{
  "igorkav/anya",
  build = "pip install -r requirements.txt",
  config = function()
    require("anya").setup({
      start_in_insert = true, -- Enter insert mode when opening the prompt
    })
  end,
}
```

### vim-plug

```vim
Plug 'igorkav/anya'
```

Then install Python dependencies manually:

```bash
cd ~/.local/share/nvim/plugged/anya  # or wherever vim-plug installs it
pip install -r requirements.txt
```

After installing, register the remote plugin once:

```
:UpdateRemotePlugins
```

Then restart Neovim.

## Configuration

### Environment Variables

Set these before launching Neovim (e.g. in your shell profile):

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENAI_API_KEY` | (required) | Your OpenAI API key |
| `ANYA_MODEL` | `gpt-4.1` | Model to use |
| `ANYA_API_KEY` | — | Override API key (e.g. for OpenRouter) |
| `ANYA_API_BASE` | — | Custom API base URL |
| `ANYA_API_TYPE` | `responses` | API type: `responses` or `chat_completions` |
| `ANYA_THINKING_BUDGET` | — | Reasoning effort hint for supported models |
| `ANYA_DISABLE_MCP` | `0` | Set to `1` to disable MCP tools |

### OpenRouter

```bash
export ANYA_API_TYPE=chat_completions
export ANYA_API_KEY="sk-or-..."
export ANYA_MODEL="anthropic/claude-opus-4-5"
nvim
```

Any model name containing `/` or `:` is automatically routed through
`https://openrouter.ai/api/v1`. You can also point `ANYA_API_BASE` at any
other OpenAI-compatible endpoint.

### Lua setup options

```lua
require("anya").setup({
  start_in_insert = true, -- Auto-enter insert mode when prompt opens (default: false)
})
```

## Terminal alias

For quick access from the terminal, add this alias to your `~/.zshrc` or `~/.bashrc`:

```bash
alias anya="nvim +Anya"
```

Then reload your shell:

```bash
source ~/.zshrc  # or source ~/.bashrc
```

Now you can open Anya directly by typing `anya` in your terminal.

## Usage

### Commands

| Command | Description |
|---------|-------------|
| `:Anya` | Toggle Anya UI (reopens last layout) |
| `:Anya open` | Open Anya UI |
| `:Anya close` | Close Anya UI |
| `:Anya tab` | Open in a new tab |
| `:Anya pane [right\|left]` | Open as a side pane |
| `:Anya send <text>` | Send a message without opening the UI |
| `:Anya cancel` | Cancel the current in-progress response |
| `:Anya history` | Open the conversation history picker |
| `:Anya daemon start` | Start the background daemon manually |
| `:Anya daemon stop` | Stop the background daemon |
| `:Anya daemon status` | Show daemon status |
| `:Anya help` | Show help text |

The daemon starts automatically when Neovim loads the plugin — you normally
don't need to manage it manually.

### Basic workflow

1. Run `:Anya` to open the chat interface
2. Type your message in the prompt window at the bottom
3. Press `<CR>` to send

### Prompt buffer keymaps

| Key | Mode | Action |
|-----|------|--------|
| `<CR>` | Normal / Insert | Send message |
| `<C-p>` | Normal / Insert | Previous prompt in history |
| `<C-n>` | Normal / Insert | Next prompt in history |
| `<C-j>` | Normal / Insert | Insert blank line |
| `<C-k>` | Normal / Insert | Focus chat window |
| `<Tab>` | Normal | Toggle focus to chat window |
| `<C-Up>` / `<C-Down>` | Normal / Insert | Increase / decrease prompt height |
| `<C-Left>` / `<C-Right>` | Normal / Insert | Resize side pane (pane layout) |
| `<C-a>` / `<C-e>` | Normal / Insert | Jump to start / end of line |
| `<C-u>` | Normal / Insert | Delete whole line |

### Chat buffer keymaps

| Key | Mode | Action |
|-----|------|--------|
| `<CR>` | Normal | Open code block / tool output, or toggle fold |
| `<Space>` | Normal | Open code block or tool output |
| `<Tab>` | Normal | Toggle focus to prompt window |
| `<C-c>` | Normal | Cancel current response |
| `]]` / `[[` | Normal | Jump to next / previous message header |
| `<localleader>h` | Normal | Open conversation history |
| `<C-j>` | Normal | Focus prompt window |

### File references and slash commands

In either buffer you can use:

- **`@path/to/file`** — include a file's contents in your message
- **`/command`** — run a built-in slash command (e.g. `/review`, `/plan`)

Both are highlighted automatically.

### Edit confirmation flow

When Anya proposes a file change, a folded block appears in the chat buffer
showing the diff. You are prompted to:

- **Apply** — write the change to disk
- **Reject** — discard the change

All edit states (pending / applied / rejected) persist across restarts.

## Conversation history

Conversations are saved to `~/.local/share/anya/conversations.db`.

Use `:Anya history` to browse and reload previous conversations.
This requires [snacks.nvim](https://github.com/folke/snacks.nvim).

## Troubleshooting

**Daemon not starting?**

```bash
# Start manually in the foreground to see errors
python -m anya.server.main --foreground
```

**Plugin not loading after install?**

Make sure you ran `:UpdateRemotePlugins` and restarted Neovim.

**Streaming issues or errors?**

Check the log:
```bash
tail -f ~/.local/share/anya/daemon.log
```

**Commands not found?**

Confirm the Python dependencies are installed in the same environment Neovim
uses. Run `:checkhealth provider` to verify.

## Architecture

See [AGENTS.md](AGENTS.md) for a full technical reference covering the agent
system, daemon design, IPC protocol, marker format, and tool extensibility.

## License

TBD
