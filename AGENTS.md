# AGENTS.md

This file provides guidance to an agent when working with code in this repository.

## Project Overview

Anya is a Neovim plugin that integrates OpenAI's Agents SDK, providing an AI assistant with conversation persistence and context awareness. It's a Python remote plugin that communicates with Neovim via pynvim.

Named after Anya Forger from Spy x Family - she can read minds, this plugin reads your code.

## Architecture

### Key Components

**Python Remote Plugin** (`rplugin/python3/anya/plugin.py`)
- Main plugin logic using `@pynvim.plugin` decorator
- `AnyaPlugin` class handles all commands and functions
- Async agent execution via background asyncio event loop
- Streaming responses with Lua animation integration

**Vim Layer** (`plugin/anya.vim`)
- Bootstrap script that sets `g:loaded_anya`
- Provides `:AnyaHistory` command for conversation browser

**Buffer Management** (`rplugin/python3/anya/buffers.py`)
- Creates split layout with chat and prompt buffers
- Chat buffer: `anya-chat` filetype for conversation display
- Prompt buffer: `anya-prompt` filetype for user input
- Streaming responses via Lua animation

**Database** (`rplugin/python3/anya/db.py`)
- SQLite database for conversation persistence
- Stores conversations and messages with metadata
- Located at `~/.local/share/anya/conversations.db`

**History Parsing** (`rplugin/python3/anya/history.py`)
- Parses buffer content to extract conversation history
- Builds LLM-compatible message history from markers
- Supports conversation and message markers

**Marker System** (`rplugin/python3/anya/markers.py`)
- Hidden HTML comment markers track message boundaries
- Markers include: `fold_start`, `fold_end`, `tool_pending`, `tool_success`, `tool_failure`
- Edit markers: `edit_pending`, `edit_applied`, `edit_rejected`, `edit_failed`

**ID Generation** (`rplugin/python3/anya/ids.py`)
- Uses hashids library for unique conversation/message IDs
- Per-installation random salt stored at `~/.local/share/anya/salt.txt`

**File Type Configuration**
- `ftplugin/anya-chat.lua`: Chat buffer settings (wrap, fold, conceal markers)
- `ftplugin/anya-prompt.lua`: Prompt buffer settings with Enter keymap
- `syntax/anya-chat.vim`: Syntax highlighting for markers

**Lua Modules** (`lua/anya/`)
- `init.lua`: Module entry point, exports all submodules
- `conversation.lua`: Conversation management and message sending
- `text.lua`: Streaming text animation and marker processing
- `markers.lua`: Marker parsing and creation utilities
- `picker.lua`: Conversation history browser (requires snacks.nvim)
- `foldtext.lua`: Custom fold text display

**Agent Definitions** (`rplugin/python3/anya/agents/`)
- `__init__.py`: Creates the `code` agent with tools
- `context.py`: `NvimPluginContext` dataclass for tool access
- `utils.py`: Helper to load prompt files

**Tools** (`rplugin/python3/anya/tools/`)
- `buffer_name.py`: Get current buffer name
- `parrot.py`: Test tool that echoes in uppercase
- `utils.py`: `nvim_call_sync` helper for thread-safe Neovim calls

### Data Flow

1. User opens interface with `:Anya` -> creates split layout with chat/prompt buffers
2. User types in prompt buffer -> presses Enter to send
3. Lua `conversation.send_message()` -> formats message, calls `AnyaSend`
4. Python `AnyaSend` -> runs agent in background asyncio loop
5. Agent streams response -> Lua animation displays text character-by-character
6. Messages saved to SQLite database with markers

### Marker Format

Markers are HTML comments that track message metadata:

```html
<!-- anya__conversation: {id}, {timestamp} -->
<!-- anya__message: {id}, start, {role}, {author}, {model}, {timestamp} -->
<!-- anya__message: {id}, end, {timestamp} -->
<!-- anya__tools: {marker_names} -->
```

## Development Commands

### Running

```vim
" Open Anya interface
:Anya

" Show help
:Anya help

" Send a prompt directly
:Anya send <text>

" Browse conversation history
:AnyaHistory
```

### Keymaps (in prompt buffer)

- `<CR>` (normal mode) - Send message
- `<CR>` (insert mode) - Exit insert and send message

### After Code Changes

```vim
" Update remote plugins after Python code changes
:UpdateRemotePlugins
" Then restart Neovim
```

### Formatting and Linting

Run the `./format` script to format and lint the codebase:

```bash
# Format only
./format

# Check formatting and run linting (no modifications)
./format --check
```

This runs:
- `ruff format` - Python formatting
- `stylua` - Lua formatting
- `luacheck` - Lua linting (only with `--check`)

### Testing

There are no automated tests for this plugin. Testing requires running Neovim and visually verifying behavior.

However, you can catch many issues before running Neovim:

```bash
# Check Python syntax
python -m py_compile rplugin/python3/anya/*.py rplugin/python3/anya/**/*.py

# Check formatting and lint
./format --check
```

## Dependencies

- Python >= 3.13
- `pynvim` - Neovim Python client
- `openai` - OpenAI Python SDK
- `openai-agents` - OpenAI Agents SDK
- `hashids` - ID generation

Optional:
- `snacks.nvim` - For conversation history picker

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | (required) | OpenAI API key |
| `ANYA_MODEL` | `gpt-4.1` | Model to use for the agent |

## Data Storage

| Data | Location |
|------|----------|
| Conversations database | `~/.local/share/anya/conversations.db` |
| ID generation salt | `~/.local/share/anya/salt.txt` |
| ID state | `~/.local/share/anya/ids.json` |

## Special Considerations

### Async Execution
- Agent runs in background asyncio event loop to prevent blocking Neovim
- Uses `nvim.async_call` for all Neovim API calls from async context
- Streaming text uses Lua timer for smooth animation

### Buffer Lifecycle
- Buffers are created fresh on each `:Anya` invocation
- Chat buffer validity is checked before operations
- Conversation ID stored as buffer variable `anya_conversation_id`

### Marker Concealment
- Markers are concealed in chat buffer using `conceallevel=2`
- Cursor on marker line temporarily reveals it
- Syntax rules in `anya-chat.vim` handle concealment

### Conversation Persistence
- Conversations auto-save to SQLite database
- Each message has unique hashid
- Conversation timestamps updated on each message

## Codebase Context

### Important Files

```
rplugin/python3/anya/
  __init__.py          # Exports AnyaPlugin, VERSION
  plugin.py            # Main plugin class with commands/functions
  buffers.py           # Buffer creation and content retrieval
  db.py                # SQLite database operations
  history.py           # Buffer content parsing for LLM
  markers.py           # Marker generation utilities
  ids.py               # Hashid generation
  agents/
    __init__.py        # Agent definition (code agent)
    context.py         # NvimPluginContext dataclass
    utils.py           # Prompt loading helpers
  tools/
    __init__.py        # Tool exports
    buffer_name.py     # Get buffer name tool
    parrot.py          # Test echo tool
    utils.py           # nvim_call_sync helper

lua/anya/
  init.lua             # Module entry point
  conversation.lua     # Message sending and conversation management
  text.lua             # Streaming animation and marker processing
  markers.lua          # Marker parsing/creation (Lua side)
  picker.lua           # Conversation history browser
  foldtext.lua         # Custom fold text

ftplugin/
  anya-chat.lua        # Chat buffer configuration
  anya-prompt.lua      # Prompt buffer configuration

plugin/anya.vim        # Bootstrap and :AnyaHistory command
syntax/anya-chat.vim   # Marker concealment syntax
prompts/               # Agent system prompts
doc/anya.txt           # Vim help documentation
```

### Prompts

| File | Purpose |
|------|---------|
| `code.md` | Main coding agent prompt (currently used) |
| `chat.md` | General conversation prompt |
| `plan.md` | Task planning prompt |
| `review.md` | Code review prompt |
| `verify.md` | Output verification prompt |
| `compact.md` | Context compaction prompt |
| `title.md` | Title generation prompt |

## Available Tools

Currently implemented tools:

1. **buffer_name** - Returns the name of the current Neovim buffer
2. **parrot** - Test tool that echoes the message in uppercase

## Vim Functions

Functions exposed to Vim/Lua:

| Function | Sync | Description |
|----------|------|-------------|
| `AnyaSend(text, conv_id?)` | async | Send prompt to agent |
| `AnyaNewConversationId()` | sync | Generate new conversation ID |
| `AnyaNewMessageId(conv_id?)` | sync | Generate new message ID |
| `AnyaTimestamp()` | sync | Get current UTC ISO timestamp |
| `AnyaSaveConversation(id, timestamp)` | sync | Save conversation to DB |
| `AnyaSaveMessage(...)` | sync | Save message to DB |
| `AnyaListConversations(limit?, offset?)` | sync | List recent conversations |
| `AnyaLoadConversation(conv_id)` | sync | Load full conversation |
| `AnyaUpdateConversationTitle(id, title)` | sync | Update conversation title |
| `AnyaDeleteConversation(conv_id)` | sync | Delete conversation |
| `AnyaRebuildBufferContent(conv_id)` | sync | Rebuild buffer from DB |

## Known Patterns

- All Neovim API calls from async contexts must use `nvim.async_call`
- String responses are split on newlines before writing to buffers
- Markers are HTML comments that get concealed in the UI
- Streaming uses Lua timer with character-by-character animation
- Database operations are lazy-initialized on first use

## Coding Guidelines for Agents

- Do not add color emojis to the codebase. Use only monospace Unicode characters or text-based indicators where visual elements are needed.
- Follow existing code conventions and formatting
- Use type hints in Python code
- Prefer minimal, focused changes over large rewrites
