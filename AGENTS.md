# AGENTS.md

This document details Anya's agent, tool, and infrastructure system. Inside, you’ll find an in-depth reference for users, contributors, and developers—covering technical architecture, extensibility, data flow, plugin/daemon boundaries, marker logic, and best practices for a robust, persistent AI Neovim assistant.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
   - [Daemon Design](#daemon-design)
   - [Component Breakdown](#component-breakdown)
   - [End-to-End Data Flow](#end-to-end-data-flow)
3. [File & Directory Structure](#file--directory-structure)
4. [Commands & Keymaps](#commands--keymaps)
5. [Editing & Streaming Logic](#editing--streaming-logic)
6. [Database and Persistence](#database-and-persistence)
7. [The Agent System & Extensibility](#the-agent-system--extensibility)
8. [The Tooling Interface](#the-tooling-interface)
9. [Marker System](#marker-system)
10. [Environment & Dependencies](#environment--dependencies)
11. [Best Practices](#best-practices)
12. [Advanced Troubleshooting](#advanced-troubleshooting)
13. [Appendix: Marker Format Reference](#appendix-marker-format-reference)

---

## Overview

**Anya** is a modern, persistent Neovim AI plugin using OpenAI's Agents SDK. It combines:
- A resilient background daemon with ZeroMQ/CBOR2 IPC
- Persistent, robust SQLite-based conversation tracking
- Multi-buffer, multi-conversation UI that survives Neovim restarts
- Streaming LLM output and interactive tool editing (via concealed buffer markers)
- Intelligent code-aware tool interface (file editing, read/search, exec, git)
- Modular agent/tool system, fully extensible in both Python and as remote MCP backends

---

## Architecture

### Daemon Design

Anya’s main process runs as a **background daemon**. It:
- Handles all AI agent execution, tool calls, conversations, and persistence
- Speaks ZeroMQ (REQ/REP + PUB/SUB) for high-performance, multi-instance support
- Tracks all state and history in `~/.local/share/anya/` (log, PID, sockets, database)
- Ensures:
  - **Persistence** across editor restarts
  - **Concurrency** (multiple Neovim clients)
  - **Isolation** of LLM/tool computation from your editor UI
  - **Streaming** feedback directly to Neovim (fast, animated, incremental)

> **Sockets:**
> - `daemon.sock`: REQ/REP (editor <-> daemon)
> - `daemon_stream.sock`: PUB/SUB (streaming output and events)
> - Log/PID also under `~/.local/share/anya/`

### Component Breakdown

- **Python Remote Plugin**: `rplugin/python3/anya/plugin.py`
    - Loads in Neovim via `pynvim`, opens buffers, handles commands
    - Manages event loop and routes user/UI events to daemon
    - Handles streaming, user input, gating for tool confirmation
- **Daemon Process**: `rplugin/python3/anya/server/`
    - `main.py`: Launches the ZeroMQ event loop, initializes sockets
    - `agents.py`: Spawns agent instances (`CodeAgent` per-user, persistent MCP Agent), manages MCP server async loading
    - `handlers.py`: Handles all request types, tool events, confirmation flows, and output streaming to UI
    - `db.py`, `history.py`, `markers.py`: Implement SQLite message storage, buffer parsing, marker management, and history recovery
- **Lua UI System**: `lua/anya/`
    - Modular client-side code: conversation API, streaming logic, extmark folding, live marker/decorator updates
    - Full integration with Neovim native folds, extmarks, and buffer UI
- **Tool System**: `rplugin/python3/anya/tools/`
    - Pluggable API for Python-side tools
    - Tools registered with agents on creation, including remote and context-aware tools

### End-to-End Data Flow

1. **User opens UI**: `:Anya` → creates chat & prompt buffers
2. **User sends message**: Buffer text handed off via ZeroMQ to daemon
3. **Daemon creates/fetches per-session agent** (includes dynamic tool registry!)
4. **Agent runs reasoning and/or tools**, streaming output and tool events to Neovim immediately
5. **Lua-side streaming handler**: Buffers incremental output, animates text with folding, marker extmarks, etc.
6. **Message boundaries and all tool operations are persisted** with markers for perfect replay/recovery
7. **If user interacts (edit accept/reject, confirm tool, etc), plugin synchronizes back to daemon**

---

## File & Directory Structure

A cross-language but developer-oriented tree:
```
rplugin/python3/anya/
├── plugin.py      # Main Neovim plugin (RPC over ZeroMQ)
├── server/        # Daemon: main, handlers, agents
├── db.py          # SQLite storage for conversations/messages
├── ids.py         # Hashid-short unique IDs
├── history.py     # Backtracking/parsing buffer with markers
├── tools/         # Modular tool system (see below)
├── agents/        # Agent config, prompt, and dynamic tool construction
├── buffers.py     # Floating window management
├── protocol.py    # IPC message types/structures
├── system_prompt.py   # Dynamic instruction merging

lua/anya/
├── init.lua       # Exported API surface for config/UIs
├── text.lua       # Streaming animation & queue
├── conversation.lua   # Sending, locking, marker placement
├── markers.lua    # Synced with Python; folding, marker helpers
├── ... (picker, foldtext, markers_ui, etc)

ftplugin/, syntax/  # Buffer local config files
prompts/            # System prompt fragments (see agent creation)
plugin/anya.vim     # Vimscript bootstrap, sets load flags
```

---

## Commands & Keymaps

**All Anya commands are under `:Anya`**:

| Command                   | Purpose                                       |
|---------------------------|-----------------------------------------------|
| `:Anya`                   | Opens chat UI (floating window)               |
| `:Anya send <text>`       | Send text without prompt buffer               |
| `:Anya history`           | Launch history browser (snacks.nvim optional) |
| `:Anya daemon status`     | Check daemon/agent status                     |
| `:Anya daemon start/stop` | Manage daemon lifecycle                       |
| `:Anya help`              | In-buffer help/docs (from plugin)             |

**Prompt Buffer Keymaps:**
- `<CR>` (Normal/Insert): Send message (exit insert mode if needed)
- See `ftplugin/anya-prompt.lua` for customizations

---

## Editing & Streaming Logic

- **Two-stage streaming:**
  - Python-side receives agent/tool chunks, passes via ZeroMQ PUB/SUB
  - Lua-side queues & animates text, applies markers, handles folding/highlighting
- **Marker-driven context**: Every message, tool result, or user action adds markers or extmarks in buffer for lossless replay. All state (conversations, messages, roles, tool results, edits, etc) can be reconstructed solely from markers and persisted text
- **Tool/edit confirmations**: If a tool (e.g., file edit) needs approval, a live tool fold is created in-buffer, user is prompted for confirmation, reply is routed to daemon (with lockout for multi-edit safety)
- **UI integrates with folds**, markers, and conceal features for a clean, readable editing experience

---

## Database and Persistence

- Conversation and message history persist in `~/.local/share/anya/conversations.db` (SQLite)
- Messages, roles, tool invocations, edits, and conversation states are all exportable and recoverable
- Per-installation short unique IDs (via hashids and local salt)
- Markers allow robust restart/crash recovery: rebuilding conversation state from buffer text is always possible

---

## The Agent System & Extensibility

- **Primary agent:** named "Code", async-initialized per session (`CodeAgent`)
  - Assembles a dynamic prompt (system + instructions + available tools)
  - Registers tools (all Python tools by default, plus MCP tool if available)
  - Exposes reasoning budget (env-configurable), model selection, and more
- **MCP agent (optional)**: Dynamic access to external/remote tools and data (e.g., context7 API, deepwiki, linkup, exa, sequentialthinking, time, dreamtap, etc.), exposed as a single super-tool for the main agent
- **Prompt directory:** prompts/ contains markdown system prompt templates; "code.md" and "mcp.md" are dynamically merged at runtime
- **To add a tool**: Implement a function in `rplugin/python3/anya/tools/`, register it in `tools/__init__.py`, and (optionally) reference in the agent configuration
- **To add an agent**: Update or create an agent class in `rplugin/python3/anya/agents/`, and register with the agent manager. New agents can specialize instruction sets, toolsets, or even expose streaming sub-task logic.

---

## The Tooling Interface

- **Standard tools** (Python side; see `tools/__init__.py`):
    - `create_file`, `edit`, `exec`, `exec_lua`, `read_file`, `read_many_files`, `list_files`, `replace_file`, `search_code`, `gh`, `parrot`, `buffer_name`
- **Tool Definitions**
    - Tools are Python functions or async methods, assigned input/output signatures (see schema in `tools` modules)
    - Tools can be called from agent reasoning, shown to user as actions, or invoked explicitly by LLM plans
    - Some tools (e.g. `edit`) require editor-side confirmation: a folding marker is inserted, user applies or rejects it, then the daemon continues
    - MCP/remote tools are loaded dynamically via network connection, appearing as a single multiplexed tool
- **Adding Tools**
    - Write your new tool in Python under the tools/ directory, register in `__init__.py`, and update agent config if optional

---

## Marker System

- **Markers are HTML comments**, embedded and concealed using Vim/Lua syntax, enabling lossless and minimally intrusive tracking
- **Message markers:** Boundaries for assistant/user/tool messages with all metadata
- **Edit/tool markers:** Indicate pending, applied, failed, etc. states for edits or tool blocks, enabling safe edit gating/undo
- **Markers govern buffer folding**, extmark placement, and region conceal; e.g., edits are shown as folded blocks until user takes an action
- **Markers always survive restarts and are recoverable from buffer or history**

---

## Environment & Dependencies

**Python:** 3.13+

**Required packages**:
  - `pynvim`, `openai`, `openai-agents`, `hashids`, `pyzmq`, `cbor2`
**Optional for advanced features**:
  - `snacks.nvim`, `stylua`, `ruff`, `luacheck`
  - Extra MCP tools: see [agent documentation](#the-agent-system--extensibility)

**Key Environment Variables**
| Variable             | Default    | Purpose                      |
|----------------------|------------|------------------------------|
| `OPENAI_API_KEY`     | (required) | LLM access                   |
| `ANYA_MODEL`         | gpt-4.1    | Default LLM                  |
| `ANYA_API_KEY`       | (unset)    | Override API key (for OpenRouter, etc.) |
| `ANYA_API_BASE`      | (unset)    | Custom API endpoint (auto-detected for OpenRouter models) |
| `ANYA_THINKING_BUDGET`| (unset)   | Reasoning effort for model   |
| `ANYA_DISABLE_MCP`   | "0"        | Disable MCP agent/tools      |
| `ANYA_YOLO`          | ""         | Approve (auto-apply) all edits|

### OpenRouter Support
Anya supports OpenRouter models out of the box. When you set `ANYA_MODEL` to an OpenRouter model
(e.g., `anthropic/claude-sonnet-4`, `openai/gpt-4o`, `deepseek/deepseek-r1`), Anya automatically:
- Detects the model uses `/` or `:` in the name (OpenRouter convention)
- Creates a custom model provider with the OpenRouter API endpoint
- Routes requests through `https://openrouter.ai/api/v1`

To use OpenRouter:
1. Set your OpenRouter API key: `export ANYA_API_KEY=sk-or-...` (or use `OPENROUTER_API_KEY`)
2. Set your model: `export ANYA_MODEL=anthropic/claude-sonnet-4`

You can also use a custom API base for other OpenAI-compatible providers:
```bash
export ANYA_API_BASE=https://your-custom-endpoint.com/v1
export ANYA_API_KEY=your-api-key
export ANYA_MODEL=your-model-name
```

---

## Best Practices

- All commands under `:Anya` (no command pollution!)
- Always update remote plugins after Python changes: `:UpdateRemotePlugins`, then restart Neovim
- Never write buffer output if streaming/queue not drained! (otherwise you risk marker/out-of-order bugs)
- Use marker and fold helpers (never fudge manual edits into the chat buffer)
- For extensibility: keep tool signatures explicit, use marker helpers/IDs, and test round-tripping with restarts and history reload
- Develop in split/test Neovim windows, especially for buffer/marker debugging

---

## Advanced Troubleshooting

- **Daemon not running?**   Use `:Anya daemon start` or run `python -m anya.server.main -f`
- **Messages incorrect/missing?**  Check marker sequence, `~/.local/share/anya/daemon.log`, and plugin synchronization
- **Streaming out of order?**   Confirm streaming queue logic in Lua is drained before making new buffer/chunk edits
- **MCP tool or external API** not responding? Check network/firewall/MCP service logs, and inspect daemon logs for agent-side exceptions
- **Marker folding or extmark bugs?**  Validate conceal rules in syntax/anya-chat.vim and Lua marker helper calls

---

## Appendix: Marker Format Reference

Markers are always hidden HTML-style comments, designed for round-tripping and replay.

```html
<!-- ac: {id}, {timestamp} -->                   # Conversation start
<!-- am: {id}, start, {role}, {author}, {model}, {timestamp} -->
<!-- am: {id}, end, {timestamp} -->               # Message end
<!-- at: {marker_names} -->                      # Tool/edit marker
<!-- af: fold_start, fold_end -->                # Folds/block regions
<!-- at: tool_pending, tool_success, tool_failure, edit_pending, edit_applied, edit_rejected, edit_failed -->
```

Markers can be extended or revised as agents and tools grow—consult the latest `markers.py` and Lua `markers.lua` for up-to-date marker schemas.

---

*This document is maintained for clarity, accuracy, and extensibility. If you add new agents, tools, or marker patterns, contribute back refinements here!*
