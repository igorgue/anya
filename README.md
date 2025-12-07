# Anya

An AI-powered Neovim plugin built on the OpenAI Agents SDK.

> Named after Anya Forger from Spy x Family - she can read minds, this plugin reads your code.

## Features

- **Chat Interface**: Split-window layout with streaming responses
- **Conversation Persistence**: SQLite database stores conversation history
- **Conversation Browser**: Browse and load previous conversations with `:Anya history`
- **Context Awareness**: Conversation history is automatically included in agent context
- **Streaming Animation**: Smooth character-by-character text animation
- **Marker System**: Hidden markers track message boundaries and metadata
- **Tool Support**: Extensible tool system using OpenAI Agents SDK

## Installation

### Prerequisites

- Neovim >= 0.9.0
- Python >= 3.13
- `pynvim` (installed globally or in your Neovim provider environment)
- `snacks.nvim` (optional, for conversation picker)

### Using [lazy.nvim](https://github.com/folke/lazy.nvim)

```lua
{
    "igor/anya",
    build = ":UpdateRemotePlugins",
    cmd = "Anya",
}
```

### Post-Installation

1. Start Neovim.
2. Run `:UpdateRemotePlugins`.
3. Restart Neovim.

### Dependencies

Install Python dependencies:

```bash
pip install pynvim openai openai-agents hashids
```

Or use the provided requirements file:

```bash
pip install -r requirements.txt
```

## Configuration

Set your OpenAI API key in your environment:

```bash
export OPENAI_API_KEY="sk-..."
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | (required) | OpenAI API key |
| `ANYA_MODEL` | `gpt-4.1` | Model to use for the agent |

## Usage

### Commands

| Command | Description |
|---------|-------------|
| `:Anya` | Open the Anya interface |
| `:Anya open` | Open the Anya interface |
| `:Anya help` | Show help message |
| `:Anya send <text>` | Send a prompt directly |
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

### Conversation History

Conversations are automatically saved to a SQLite database at `~/.local/share/anya/conversations.db`.

Use `:Anya history` to browse and load previous conversations (requires `snacks.nvim`).

## Architecture

### Buffer Types

- **anya-chat**: Main chat buffer displaying conversation history
- **anya-prompt**: Input buffer for composing messages

### Data Storage

| Data | Location |
|------|----------|
| Conversations database | `~/.local/share/anya/conversations.db` |
| ID generation salt | `~/.local/share/anya/salt.txt` |
| ID state | `~/.local/share/anya/ids.json` |

### Available Tools

The agent currently has access to:

- `buffer_name` - Get the name of the current buffer
- `parrot` - Test tool that echoes messages in uppercase

## Project Instructions

Create an `AGENTS.md` file in your project root to provide custom instructions to the agent. These instructions are prepended to the agent's system prompt.

## Development

### File Structure

```
anya/
├── rplugin/python3/anya/     # Python remote plugin
│   ├── plugin.py             # Main plugin class
│   ├── buffers.py            # Buffer management
│   ├── db.py                 # SQLite database
│   ├── history.py            # Conversation parsing
│   ├── markers.py            # Message markers
│   ├── ids.py                # ID generation
│   ├── agents/               # Agent definitions
│   └── tools/                # Tool implementations
├── lua/anya/                 # Lua modules
│   ├── init.lua              # Module entry point
│   ├── conversation.lua      # Conversation management
│   ├── text.lua              # Streaming animation
│   ├── markers.lua           # Marker utilities
│   ├── picker.lua            # History picker
│   └── foldtext.lua          # Custom fold text
├── ftplugin/                 # Filetype configuration
│   ├── anya-chat.lua         # Chat buffer settings
│   └── anya-prompt.lua       # Prompt buffer settings
├── plugin/anya.vim           # Bootstrap commands
├── syntax/anya-chat.vim      # Syntax highlighting
├── prompts/                  # Agent system prompts
└── doc/anya.txt              # Vim help
```

### Updating Remote Plugins

After modifying Python code:

```vim
:UpdateRemotePlugins
```

Then restart Neovim.

## License

MIT
