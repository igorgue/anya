# AGENTS.md

This file provides guidance to an agent when working with code in this repository.

## Project Overview

Anya is a Neovim plugin that integrates OpenAI's Agents SDK, providing an AI assistant with conversation persistence and context awareness. It's a Python remote plugin that communicates with Neovim via pynvim.

Named after Anya Forger from Spy x Family - she can read minds, this plugin reads your code.

## Architecture

### Daemon Architecture

Anya uses a standalone daemon process for agent execution, communicating with Neovim via ZeroMQ IPC and CBOR2 serialization.

```
Neovim Plugin <--ZeroMQ IPC--> Daemon Server <--> Agent Execution
     |                              |
     |                              +-- MCP Agent (singleton, always running)
     +-- Streaming via PUB/SUB      +-- Code Agent (per session)
```

**Benefits:**
- Daemon persists across Neovim restarts
- Single daemon serves multiple Neovim instances
- MCP connections remain active for faster first message
- Agent state maintained between requests

### Daemon Components

**Daemon Server** (`rplugin/python3/anya/server/`)
- `main.py`: Main daemon process with ZeroMQ sockets
- `agents.py`: Agent lifecycle management (MCP singleton, Code per-session)
- `handlers.py`: Request handlers for agent operations

**Client Library** (`rplugin/python3/anya/client.py`)
- ZeroMQ client for plugin-to-daemon communication
- Request/response and streaming subscription

**Protocol** (`rplugin/python3/anya/protocol.py`)
- CBOR2-serialized message types: Request, Response, StreamChunk
- Request types: SEND_MESSAGE, CANCEL_REQUEST, GET_STATUS, END_SESSION, SHUTDOWN

**Daemon Management** (`rplugin/python3/anya/daemon.py`)
- Start/stop daemon process
- PID file and socket path management

### Daemon Files

| File | Location |
|------|----------|
| PID file | `~/.local/share/anya/daemon.pid` |
| REQ/REP socket | `~/.local/share/anya/daemon.sock` |
| PUB/SUB socket | `~/.local/share/anya/daemon_stream.sock` |
| Log file | `~/.local/share/anya/daemon.log` |

### Running the Daemon

```bash
# Start in background (default)
python -m anya.server

# Start in foreground (for debugging)
python -m anya.server --foreground

# With debug logging
python -m anya.server --foreground --debug

# Or use the installed script
anya-daemon --foreground
```

### Daemon Commands in Neovim

```vim
:Anya daemon status    " Check daemon status
:Anya daemon start     " Start daemon
:Anya daemon stop      " Stop daemon
:Anya daemon restart   " Restart daemon
```

The daemon is automatically started by the plugin if not running.

### Key Components

**Python Remote Plugin** (`rplugin/python3/anya/plugin.py`)
- Main plugin logic using `@pynvim.plugin` decorator
- `AnyaPlugin` class handles all commands and functions
- Communicates with daemon via ZeroMQ client
- Streaming responses with Lua animation integration

**Vim Layer** (`plugin/anya.vim`)
- Bootstrap script that sets `g:loaded_anya`

**Buffer Management** (`rplugin/python3/anya/buffers.py`)
- Creates floating chat and prompt windows (chat fills space, prompt docked to last 3 lines)
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

1. User opens interface with `:Anya` -> creates floating chat/prompt windows
2. User types in prompt buffer -> presses Enter to send
3. Lua `conversation.send_message()` -> formats message, calls `AnyaSend`
4. Python plugin sends request to daemon via ZeroMQ
5. Daemon executes agent, streams responses via PUB socket
6. Plugin subscribes to stream, displays text via Lua animation
7. Messages saved to SQLite database with markers

### Marker Format

Markers are HTML comments that track message metadata:

```html
<!-- ac: {id}, {timestamp} -->
<!-- am: {id}, start, {role}, {author}, {model}, {timestamp} -->
<!-- am: {id}, end, {timestamp} -->
<!-- at: {marker_names} -->
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
:Anya history
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
- `pyzmq` - ZeroMQ for daemon IPC
- `cbor2` - CBOR serialization for messages

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
| Daemon PID file | `~/.local/share/anya/daemon.pid` |
| Daemon log file | `~/.local/share/anya/daemon.log` |
| Daemon REQ/REP socket | `~/.local/share/anya/daemon.sock` |
| Daemon PUB/SUB socket | `~/.local/share/anya/daemon_stream.sock` |

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
  client.py            # ZeroMQ client for daemon communication
  protocol.py          # CBOR message protocol definitions
  daemon.py            # Daemon lifecycle management
  buffers.py           # Buffer creation and content retrieval
  db.py                # SQLite database operations
  history.py           # Buffer content parsing for LLM
  markers.py           # Marker generation utilities
  ids.py               # Hashid generation
  server/
    __init__.py        # Daemon package
    main.py            # Daemon main loop with ZeroMQ sockets
    agents.py          # Agent lifecycle (MCP singleton, Code per-session)
    handlers.py        # Request handlers for agent operations
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

plugin/anya.vim        # Bootstrap script only
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

## Request Lifecycle Events

The plugin emits User autocommand events to track request state. These are useful for integrations (e.g., status indicators, blocking UI during requests).

### Events

| Event | Data | Description |
|-------|------|-------------|
| `AnyaRequestStarted` | `{id, model}` | Fired when Python agent starts processing |
| `AnyaRequestFinished` | `{id, status}` | Fired when Python agent completes (`status`: "success" or "error") |

### Listening to Events

```lua
vim.api.nvim_create_autocmd("User", {
  pattern = "AnyaRequestStarted",
  callback = function(event)
    local request_id = event.data.id
    local model = event.data.model
    -- Handle request start
  end,
})

vim.api.nvim_create_autocmd("User", {
  pattern = "AnyaRequestFinished",
  callback = function(event)
    local request_id = event.data.id
    local status = event.data.status  -- "success" or "error"
    -- Handle request completion
  end,
})
```

### Tracking Complete Streaming State

**Important**: `AnyaRequestFinished` fires when the Python agent completes, but the Lua streaming queue may still be animating text. To check if streaming is truly complete (both agent done AND queue empty), use:

```lua
local conversation = require("anya.conversation")

-- Returns true if agent is running OR queue has pending text
if conversation.is_request_in_progress() then
  -- Still streaming
end
```

Or check the queue directly:

```lua
local text = require("anya.text")
local status = text.get_queue_status()
-- status.queue_length: number of items in queue
-- status.timer_running: whether animation timer is active
```

## Streaming Queue Architecture

The plugin uses a **two-stage streaming system**:

1. **Python side** (`plugin.py`): Queues text via `nvim.async_call` to Lua
2. **Lua side** (`text.lua`): Animates text character-by-character via `_G.anya_stream_queue`

### Critical: Waiting for Streaming to Complete

When Python calls `nvim.async_call(self._stream_text_to_buffer, ...)`, the text is added to a Lua queue but **not yet written to the buffer**. The Lua timer processes the queue asynchronously.

**This means**: Any code that needs to render content AFTER previous content must wait for BOTH:
1. The Python-side state (e.g., `g:anya_tool_fold_open`)
2. The Lua streaming queue to be empty

### Tool Fold State Tracking

The plugin tracks open tool folds via:
- `self._tool_fold_open` (Python)
- `g:anya_tool_fold_open` (Vim global, for tools to read)

Set to `true` when `fold_start` is queued, `false` when `fold_end` is queued.

### Pattern: Waiting for Buffer to be Ready

From an async Python tool, wait for both fold state AND queue:

```python
async def _wait_for_streaming_complete(nvim, timeout: float = 300.0) -> None:
    start_time = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start_time < timeout:
        state = [{"fold_open": False, "queue_length": 0}]

        def get_state():
            try:
                fold_open = nvim.eval("get(g:, 'anya_tool_fold_open', v:false)")
                queue_status = nvim.exec_lua(
                    "return require('anya.text').get_queue_status()"
                )
                state[0] = {
                    "fold_open": bool(fold_open),
                    "queue_length": queue_status.get("queue_length", 0),
                }
            except Exception:
                state[0] = {"fold_open": False, "queue_length": 0}

        nvim.async_call(get_state)
        await asyncio.sleep(0.05)

        if not state[0]["fold_open"] and state[0]["queue_length"] == 0:
            return
```

### Why This Matters

Without waiting for the queue, content can appear in wrong locations:
- Tool A outputs header + fold_start + content (queued)
- Tool B tries to render immediately (before queue drains)
- Tool B's content appears INSIDE Tool A's fold

The `edit` tool uses this pattern to ensure edit blocks render after other tool folds close.

## Known Patterns

- All Neovim API calls from async contexts must use `nvim.async_call`
- String responses are split on newlines before writing to buffers
- Markers are HTML comments that get concealed in the UI
- Streaming uses Lua timer with character-by-character animation
- Database operations are lazy-initialized on first use
- **Tools that render UI must wait for streaming queue to be empty**

## Coding Guidelines for Agents

- Do not add color emojis to the codebase. Use only monospace Unicode characters or text-based indicators where visual elements are needed.
- Follow existing code conventions and formatting
- Use type hints in Python code
- Prefer minimal, focused changes over large rewrites

## Command System

**IMPORTANT**: All Anya commands must be implemented as subcommands of `:Anya`. Do NOT create new standalone commands like `:AnyaHistory`.

Examples:
- ✅ Correct: `:Anya history`, `:Anya send <text>`, `:Anya help`
- ❌ Incorrect: `:AnyaHistory`, `:AnyaSend`, `:AnyaHelp`

The main `:Anya` command in `plugin.py` uses `nargs="*"` to accept subcommands and their arguments. This keeps the command namespace clean and ensures all functionality is discoverable under `:Anya`.

## Documentation

Documentation for the OpenAI Agents SDK can be found at: `docs/openai-agents/` please look there for more information on how to work with agents and tools.
