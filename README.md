# Anya

A persistent Neovim AI assistant with multi-conversation support, built on OpenAI's Agents SDK.

## Features

- **Persistent Conversations**: Conversations survive Neovim restarts via SQLite storage
- **Multi-Layout Support**: Toggle between replace, pane, tab, and split layouts
- **Streaming Output**: Real-time LLM responses with animated text rendering
- **Prompt History**: Cycle through previous prompts with `<C-p>` and `<C-n>`
- **Context Awareness**: Automatic inclusion of selected code and file references
- **Conversation Browser**: Browse and load previous conversations with `:Anya history`
- **Intelligent Code Awareness**: File editing, searching, and execution tools
- **Modular Architecture**: Extensible agent and tool system in both Python and Lua

## Installation

```vim
Plug 'igorkav/anya'  " using vim-plug
```

### Requirements

**Python:** 3.13+

**Required packages:**
- `pynvim`
- `openai`
- `openai-agents`
- `hashids`
- `pyzmq`
- `cbor2`

**Optional for advanced features:**
- `snacks.nvim`
- `img-clip.nvim`
- `blink.cmp`

Install Python dependencies:
```bash
pip install -r requirements.txt
```

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENAI_API_KEY` | (required) | LLM access |
| `ANYA_MODEL` | gpt-4.1 | Default LLM |
| `ANYA_API_KEY` | (unset) | Override API key (for OpenRouter, etc.) |
| `ANYA_API_BASE` | (unset) | Custom API endpoint |
| `ANYA_API_TYPE` | responses | API type: "responses" or "chat_completions" |
| `ANYA_THINKING_BUDGET` | (unset) | Reasoning effort for model |
| `ANYA_DISABLE_MCP` | "0" | Disable MCP agent/tools |
| `ANYA_YOLO` | "" | Approve all edits automatically |

## Usage

### Commands

| Command | Description |
|---------|-------------|
| `:Anya` | Toggle Anya UI |
| `:Anya open` | Open Anya UI |
| `:Anya pane` | Open in side pane layout |
| `:Anya tab` | Open in tab layout |
| `:Anya send <text>` | Send text without opening UI |
| `:Anya history` | Open conversation history picker |

### Basic Workflow

1. Run `:Anya` to open the chat interface
2. Type your message in the bottom prompt window
3. Press `Enter` to send

### Keymaps (in prompt buffer)

| Key | Mode | Action |
|-----|------|--------|
| `<CR>` | Normal | Send message |
| `<CR>` | Insert | Exit insert mode and send message |
| `<C-p>` | Normal/Insert | Navigate to previous (older) prompt in history |
| `<C-n>` | Normal/Insert | Navigate to next (newer) prompt in history |
| `<C-j>` | Normal/Insert | Insert blank line |
| `<C-k>` | Normal/Insert | Focus chat window |
| `<C-Up>` / `<C-Down>` | Normal/Insert | Increase/decrease prompt height |
| `<C-Left>` / `<C-Right>` | Normal/Insert | Resize side pane (pane layout) |
| `<Tab>` | Normal | Toggle focus between chat and prompt |
| `q` | Normal | Close Anya |

### Conversation History

Conversations are automatically saved to a SQLite database at `~/.local/share/anya/conversations.db`.

Use `:Anya history` to browse and load previous conversations (requires `snacks.nvim`).

### Prompt History

Previously sent prompts are automatically saved and can be cycled through using:

- `<C-p>` (Previous) - Go to older prompts
- `<C-n>` (Next) - Go to newer prompts
- Pressing `<CR>` or typing stops navigation and starts a new prompt

History is stored at `~/.local/share/anya/prompt_history.txt`.

## Architecture

> How many programmers does it take to change a light bulb? None, that's a hardware problem!

### Buffer Types

- **anya-chat**: Main chat buffer displaying conversation history
- **anya-prompt**: Input buffer for typing messages

### Data Flow

1. User types message → Prompt buffer
2. Press `<CR>` → Message sent via ZeroMQ to daemon
3. Daemon processes with agent (OpenAI) → Streams response back
4. Lua handles streaming → Animated rendering in chat buffer
5. All events persisted to SQLite database

### Tool System

Anya includes intelligent tools for code interaction:

- `read_file`, `edit`, `replace_file` - File operations
- `search_code` - Search codebase with ripgrep
- `exec`, `exec_lua` - Run shell/Lua commands
- `gh` - GitHub CLI integration
- And more (MCP tools for external APIs)

### Edit Tool Workflow

The `edit` tool provides interactive, confirmation-based file editing:

1. **Agent proposes changes**: When the agent needs to edit a file, it generates SEARCH/REPLACE blocks
2. **Marker creation**: Edit markers are embedded in the chat buffer as hidden HTML comments, tracking:
   - Pending edits (waiting for your approval)
   - Applied edits (changes successfully written)
   - Rejected edits (changes you declined)
   - Failed edits (errors occurred during application)
3. **Folded preview**: Proposed edits appear as folded blocks in the chat buffer
4. **User confirmation**: You're prompted to:
   - **Apply** the changes to the file
   - **Reject** the changes entirely
5. **State persistence**: All edit states (pending/applied/rejected) persist across restarts via markers

This approach ensures:
- **Safety**: No changes without explicit approval
- **Replayability**: Complete history of proposed and actual changes
- **Crash recovery**: Edit state can be recovered from buffer markers alone

**Auto-approval**: Set `ANYA_YOLO` environment variable to automatically apply all edits without confirmation.

## Configuration

```lua
-- Lazy.nvim example
{
  'igorkav/anya',
  config = function()
    require('anya').setup({
      start_in_insert = true,  -- Enter insert mode when opening prompt
    })
  end
}
```

## Development

See [AGENTS.md](AGENTS.md) for detailed documentation on the agent system, marker format, and architecture.

### Project Structure

```
anya/
├── rplugin/python3/anya/
│   ├── plugin.py          # Main Neovim plugin
│   ├── server/            # Daemon process
│   ├── db.py              # SQLite database
│   ├── buffers.py         # Window/buffer management
│   ├── history.py         # Python history integration
│   └── tools/             # Modular tool system
├── lua/anya/
│   ├── init.lua           # Public API
│   ├── history.lua        # Prompt history management
│   ├── conversation.lua    # Message sending logic
│   ├── text.lua           # Streaming animation
│   └── markers.lua        # Marker handling
└── prompts/               # System prompt templates
```

### Testing History Navigation

After installation, you can test the prompt history:

1. Open Anya with `:Anya`
2. Type a message and press `<CR>` to send it
3. Send another different message
4. In the prompt buffer, press `<C-p>` to cycle back to previous prompts
5. Press `<C-n>` to cycle forward
6. Start typing to exit navigation mode and compose a new message

## Troubleshooting

**Daemon not running?**
```bash
python -m anya.server.main -f  # Start daemon in foreground
```

**Streaming issues?**
Check logs at `~/.local/share/anya/daemon.log`

**Missing prompts after restart?**
Prompts are saved to `~/.local/share/anya/prompt_history.txt`

## License

MIT

---

Made with love by the Anya community
