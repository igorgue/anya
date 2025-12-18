# AGENTS.md

This document explains the design, usage, and contribution workflow for the Anya agent system—a Neovim AI assistant that persists conversations and interacts contextually with your code editor.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
   - [Daemon Architecture](#daemon-architecture)
   - [Component Breakdown](#component-breakdown)
   - [Process & Data Flow](#process--data-flow)
3. [File & Directory Structure](#file--directory-structure)
4. [Commands & Keymaps](#commands--keymaps)
5. [Editing & Streaming System](#editing--streaming-system)
6. [Database and Persistence](#database-and-persistence)
7. [Extending Anya (Agents & Tools)](#extending-anya-agents--tools)
8. [Environment & Dependencies](#environment--dependencies)
9. [Best Practices](#best-practices)
10. [Troubleshooting](#troubleshooting)
11. [Appendix: Marker Format](#appendix-marker-format)

---

## Overview

**Anya** is a Neovim plugin using OpenAI's Agents SDK to provide fast, contextual AI assistance in your code editor. It features persistent conversations, context tracking (via buffer content and markers), and a robust, modular, ZeroMQ-based backend.

- **Main Features:**
  - Fast response via a persistent background daemon
  - Multi-buffer, multi-conversation capability
  - Streaming LLM output directly into the editor, as you type and edit
  - SQLite-based conversation and message history
  - Message boundaries and tool output are tracked with hidden markers
  - Extensible with your own agents and tools

---

## Architecture

### Daemon Architecture

Anya's core runs as an independent background server ("daemon") that communicates with Neovim through ZeroMQ sockets using CBOR2 serialization.

```
Neovim Plugin <-> ZeroMQ IPC <-> Daemon Server <-> Agents (MCP + Code Agents)
        |                           |
        |                           +-- MCP Agent (singleton, persistent)
        +-- Streaming (PUB/SUB)     +-- Code Agent (per session)
```

**Advantages:**
- **Persistence:** Survives Neovim restarts, enables instant reconnects, and caches state
- **Concurrency:** Handles multiple Neovim instances and conversations at once
- **Performance:** Maintains live connections to MCP (lower latency)
- **Isolation:** Protects Neovim event loop from blocking LLM calls or expensive computation

#### Communication Sockets

| Purpose       | Path                                        |
|---------------|---------------------------------------------|
| REQ/REP       | `~/.local/share/anya/daemon.sock`           |
| PUB/SUB       | `~/.local/share/anya/daemon_stream.sock`    |
| Log file      | `~/.local/share/anya/daemon.log`            |
| PID file      | `~/.local/share/anya/daemon.pid`            |

### Component Breakdown

**Python Remote Plugin** (`rplugin/python3/anya/plugin.py`)
- Uses `pynvim` API and manages commands, buffer lifecycles, and streaming

**Daemon Server** (`rplugin/python3/anya/server/`)
- **main.py**: Top-level process: starts sockets, loads agents, runs event loops
- **agents.py**: Handles Agent lifecycles (singleton MCP, per-session code agent)
- **handlers.py**: Dispatches incoming requests/events

**Client Library** (`rplugin/python3/anya/client.py`)
- Encapsulates all ZeroMQ IPC for communication between editor and daemon

**Other Key Modules:**
- `db.py` (SQLite conversation DB)
- `history.py` (parsing markers and buffer content)
- `buffers.py` (buffer creation, floating windows, prompt and chat panes)
- `markers.py` (marker encoding/decoding)
- `ids.py` (hashid-based unique ID generation)
- `tools/` (custom tool definitions, e.g., `buffer_name`, `parrot`)

### Process & Data Flow

1. **User opens Anya UI** (`:Anya`)
2. **Buffers created** for chat and prompt
3. **User sends message**; entered text goes into prompt buffer
4. **Plugin sends request** via ZeroMQ to daemon
5. **Daemon executes agent**, streaming output (chunked tokens or tool responses) back
6. **Plugin displays and animates AI replies** in chat buffer; all output is persisted in SQLite

---

## File & Directory Structure

A summarized, task-focused index of important files and their roles:

```
rplugin/python3/anya/
├── plugin.py            # Entrypoint; sets up commands, buffer management
├── client.py            # ZeroMQ client
├── protocol.py          # Request/response message definitions
├── daemon.py            # Daemon process lifecycle commands
├── buffers.py           # Floating window logic, content management
├── db.py                # Models and CRUD for conversations/messages
├── history.py           # Logic for parsing messages from buffers
├── markers.py           # HTML-like markers for conversation state
├── ids.py               # Stable short IDs, via hashids
├── server/
│   ├── main.py          # Daemon process: event loop, socket setup
│   ├── agents.py        # Agent and tool lifecycles
│   ├── handlers.py      # Handles requests: message, tool, status, etc.
├── agents/              # Agent configuration and constructors
│   ├── __init__.py
│   ├── context.py
│   ├── utils.py
├── tools/               # Tool implementations (buffer_name, parrot, etc.)
│   ├── __init__.py
│   ├── buffer_name.py
│   ├── parrot.py
│   ├── utils.py

lua/anya/
├── init.lua             # Lua-side API surface (require('anya'))
├── conversation.lua     # Send/manage conversations from Lua
├── text.lua             # Handles animation, streaming, queue
├── markers.lua          # Marker helpers in Lua
├── picker.lua           # UI: conversation browser (needs snacks.nvim)
├── foldtext.lua         # Custom fold rendering

ftplugin/anya-chat.lua   # Bufferfile settings for chat buffer
ftplugin/anya-prompt.lua # Bufferfile settings for prompt buffer
syntax/anya-chat.vim     # Syntax & conceal for markers

prompts/                 # System prompt fragments for agents
doc/anya.txt             # :help anya documentation

plugin/anya.vim          # Vim bootstrap; just loads Python plugin
```

---

## Commands & Keymaps

### User Commands

**ALL commands are subcommands of `:Anya` (convention!):**

| Command                   | Purpose                                   |
|---------------------------|-------------------------------------------|
| `:Anya`                   | Opens chat UI in a floating window        |
| `:Anya send <text>`       | Sends text directly (bypasses UI)         |
| `:Anya history`           | Opens conversation history UI             |
| `:Anya daemon status`     | Query daemon state                        |
| `:Anya daemon start`      | Start daemon if not running               |
| `:Anya daemon stop`       | Stop daemon                               |
| `:Anya daemon restart`    | Restart the daemon                        |
| `:Anya help`              | Show help documentation                   |

### Buffer Keymaps (in prompt buffer)

- `<CR>` (Normal) – Send message
- `<CR>` (Insert) – Exit insert and send
- (See `ftplugin/anya-prompt.lua` for more customizations)

---

## Editing & Streaming System

Anya displays LLM output and tool messages as *animated, streaming text*. This system is two-stage:

1. **Python-side**: Schedules streaming to Lua via batched text chunks
2. **Lua-side**: Animates text character-by-character in UI

**Markers** are hidden HTML comments, used to track boundaries, roles, tool output, etc. These markers are "concealed" with syntax rules in chat buffer, but always present in persisted content—enabling precise reconstruction for AI context.

#### Streaming Queue Details

- Python pushes messages and queues marker states
- Lua handles a buffer-based animation queue and visible output
- Tools/scripts **must** wait until the streaming queue is empty before making output edits, to avoid out-of-order rendering

---

## Database and Persistence

**SQLite database**: Tracks every conversation and message with stable IDs, authors, timestamps, and role data.

- Default DB: `~/.local/share/anya/conversations.db`
- Each message and conversation is uniquely identified (using salt+hashids)
- Buffer content is parsed on demand to reconstruct message boundaries (using markers)

---

## Extending Anya (Agents & Tools)

Anya is designed for customization and extension.

### Creating Agents

- Agents are defined in `rplugin/python3/anya/agents/`
- Each agent can expose its own tools, system prompt, and context
- Main agent (`code`) is currently primary, but more can be added for specialized flows

### Writing Tools

- Place new tool implementations in `rplugin/python3/anya/tools/`
- Tools follow a defined API: input/output spec, registered in `tools/__init__.py`
- Example tools: 
    - `parrot` (echoes input in uppercase)
    - `buffer_name` (shows current buffer)

### Lua-side Extensions

- The Lua module `lua/anya/init.lua` and submodules can be required and used in user config or for UI enhancements

---

## Environment & Dependencies

**Python**: 3.13+ required

Install dependencies (minimal):

- `pynvim`
- `openai`
- `openai-agents`
- `hashids`
- `pyzmq`
- `cbor2`

Optional for enhanced features:

- `snacks.nvim` (fuzzy picker/browsing)
- `stylua`, `ruff`, `luacheck` (formatting/linting)

### Environment Variables

| Variable           | Default    | Purpose                          |
|--------------------|------------|----------------------------------|
| `OPENAI_API_KEY`   | (required) | API key for OpenAI endpoints     |
| `ANYA_MODEL`       | gpt-4.1    | Default model for LLM requests   |

---

## Best Practices

- **Edit as needed, but keep commands under `:Anya`**, never add `:AnyaHistory` etc.
- **Always update remote plugins** after Python changes:  
  `:UpdateRemotePlugins` → Restart Neovim
- **Respect marker logic**: Never write or edit buffer output if streaming queue isn't drained!
- **Add tests/debug in real Neovim**; automated test coverage is currently minimal.
- **Use provided formatting/linting scripts** (see below).
- **Never add color emojis**—use only monochrome Unicode or plain text for all UI elements.

---

## Troubleshooting

- **Daemon isn’t running?** Use `:Anya daemon start` or run `python -m anya.server --foreground`.
- **Message output garbled/missing?**  
  - Check correct ordering (are markers present?)
  - Investigate log file: `~/.local/share/anya/daemon.log`
- **Plugin changes not visible?**  
  - Run `:UpdateRemotePlugins` and restart Neovim.
- **Streaming/text out of order?**  
  - Ensure your tool or agent waits for streaming queue to drain before output.

---

## Appendix: Marker Format

Markers are special concealed comments, ensuring messages/boundaries are tracked and can be parsed out of buffer history even after many edits.

Sample markers:

```html
<!-- ac: {id}, {timestamp} -->

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

---

*This document was last comprehensively revised in December 2025 for clarity, organization, and actionable reference. Please contribute back improvements if you extend Anya's agent or tool ecosystem.*
