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
9. [Agent Libraries](#agent-libraries)
10. [Marker System](#marker-system)
11. [Environment & Dependencies](#environment--dependencies)

> **Note:** As of 2026, Anya manages all dependencies via `pyproject.toml` only.
> You no longer need (or should create) a `requirements.txt`. Just use `uv sync --upgrade` whenever you need to update or set up your Python packages.
12. [Best Practices](#best-practices)
13. [Advanced Troubleshooting](#advanced-troubleshooting)
14. [Appendix: Marker Format Reference](#appendix-marker-format-reference)

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
    - `agents.py`: Spawns agent instances (`Agent` per-user, persistent MCP Agent), manages MCP server async loading
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
| `:Anya`                   | Toggle UI (reopens last layout)               |
| `:Anya open`              | Open Anya UI                                  |
| `:Anya close`             | Close Anya UI                                 |
| `:Anya toggle`            | Toggle pane visibility                        |
| `:Anya tab`               | Open in a new tab                             |
| `:Anya pane [right\|left]`| Open as a side pane                           |
| `:Anya send <text>`       | Send text without prompt buffer               |
| `:Anya do "<instruction>"`| Run headless buffer-edit workflow             |
| `:Anya cancel`            | Cancel in-progress chat or `:Anya do` request |
| `:Anya history`           | Launch history browser (snacks.nvim optional) |
| `:Anya system-prompt`     | Show effective expanded system prompt         |
| `:Anya daemon status`     | Check daemon status                           |
| `:Anya daemon start/stop/restart` | Manage daemon lifecycle               |
| `:Anya copilot login/logout/status/models` | Manage Copilot auth/models    |
| `:Anya help`              | In-buffer help/docs (from plugin)             |

**Prompt Buffer Keymaps:**
- `<CR>` (Normal/Insert): Send message (exit insert mode if needed)
- See `ftplugin/anya-prompt.lua` for customizations

---

## Editing & Streaming Logic

- **Two-stage streaming:**
  - Python-side receives agent/tool chunks, passes via ZeroMQ PUB/SUB
  - Lua-side queues & animates text, applies markers, handles folding/highlighting
- **Request lifecycle tracking**: `conversation.lua` tracks send-lock + request-lock separately, clears stale queue state, and auto-drains queued prompts after response completion
- **Send/cancel semantics**: if a request is in progress, the latest prompt replaces pending queue state and current execution is cancelled so the newest prompt runs next
- **Marker-driven context**: Every message, tool result, or user action adds markers or extmarks in buffer for lossless replay. All state (conversations, messages, roles, tool results, edits, etc) can be reconstructed solely from markers and persisted text
- **Autoscroll and sync barriers**: streaming uses explicit autoscroll tracking and sync-write barriers so marker writes (for example fold boundaries) are ordered safely against queued animated chunks
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

- **Primary chat agent:** `Agent`, cached per session with a composite cache key (request kind + settings hash + cwd + skills fingerprint + memory fingerprint)
  - Assembles dynamic instructions from `prompts/code.md`, auto-discovered libs docs, system context, project `AGENTS.md`, and discovered skills metadata
  - Uses per-client `AgentSettings` when provided, with environment fallback
  - Exposes reasoning/model/provider controls through settings/env
- **Headless edit agent:** `DoAgent`, a lightweight path for `:Anya do` focused on fast buffer modifications with the same provider/settings model
- **MCP integration model**: MCP tools are loaded dynamically from user config and surfaced through the libs/tool prompt path; there is no separate static "MCP agent" class in the runtime architecture
- **Prompt directory:** prompt templates currently live in `prompts/` (for example `code.md`, `plan.md`, `title.md`, `compact.md`)
- **To add a tool**: Implement a function in `rplugin/python3/anya/tools/`, register it in `tools/__init__.py`, and (optionally) reference in the agent configuration
- **To add an agent**: Update or create an agent class in `rplugin/python3/anya/agents/`, and register with the agent manager. New agents can specialize instruction sets, toolsets, or even expose streaming sub-task logic.

---

## The Tooling Interface

### Single-Tool Architecture

**Anya uses one and ONLY one tool: `execute`.** All agent capabilities are exposed as **libs** (`anya.libs.*`), not as separate tools. The agent calls `execute` with Python code that imports and uses the appropriate lib. This keeps the tool surface minimal, avoids tool sprawl, and makes the agent's capabilities composable via normal Python code.

- **Never add new tools.** If you need a new capability, implement it as a lib under `rplugin/python3/anya/libs/` and use it via `execute`.
- The only exception is MCP tools, which are loaded dynamically as a single multiplexed remote tool.

### Standard Libs (used via `execute`)
    - `fs` — `create_file`, `read_file`, `read_many_files`, `list_files`, `search_code`, `write_file`
    - `shell` — `run` (exec), `gh` (GitHub CLI)
    - `buffer` — `modify`, `modify_file`, `list_open_buffers`, `is_open`
    - `ui` — `ask`, `confirm`, `input`
    - `web` — `fetch_markdown`, `fetch_text`, `fetch_json`
    - `search` — `web`, `news`
    - `mcp` — `call` (remote MCP server tools)
    - `background` — `list_jobs`, `get_job`, `tail_logs`, `read_logs`, `is_running`, `stop_job`, `wait_for_job`
    - `memory` — memory search/format helpers
    - `task_list` — structured task tracking utilities
    - `playwright` — browser automation helpers
    - `skills` — read/discover skill metadata and docs

### Adding New Capabilities
    - Write your new lib as a Python module under `rplugin/python3/anya/libs/`
    - Export it from `rplugin/python3/anya/libs/__init__.py`
    - Document it in this file and in the agent prompts
    - **Do NOT create a new tool** — use `execute` with the new lib instead


---

## Agent Libraries

The agent has access to pre-built libraries that provide optimized, context-rich operations.
**Agents should always prefer these libraries over raw Python operations.**

### File System (`fs`)

```python
from anya.libs import fs
```

| Function | Description |
|----------|-------------|
| `read_file(path_with_range, cwd=None)` | Read file with line numbers. Default 300 lines. Use `file.py@start-end` syntax for ranges. |
| `read_many_files(paths)` | Read multiple files efficiently in one call. |
| `list_files(path=".", max_results=200, cwd=None)` | List files recursively (respects .gitignore via fd). |
| `search_code(query, path=None, max_chars=4000, cwd=None)` | Search for patterns using ripgrep. |
| `create_file(path, content=None, lines=None, cwd=None)` | Create a new file (raises if exists). |
| `write_file(path, content=None, lines=None, cwd=None)` | Write to file, creating directories as needed. |

### Shell & Git (`shell`)

```python
from anya.libs import shell
```

| Function | Description |
|----------|-------------|
| `run(command)` | Execute a shell command and return output. |
| `gh(args)` | Execute GitHub CLI commands. |

### MCP Integration (`mcp`)

```python
from anya.libs import mcp

# Call tools on MCP servers
result = mcp.call("server_name", "tool_name", {"arg": "value"})
```

**MCP server discovery is dynamic:**
- Server configs are loaded from `~/.config/anya/mcp/servers.json`
- Tool listings are cached in `~/.local/share/anya/mcp_tools_cache.json`
- Use `mcp.list_servers()` / `mcp.list_tools(server)` to inspect active runtime availability

### Web Operations (`web`, `search`)

```python
from anya.libs import web, search

# Fetch web content
web.fetch_markdown(url)
web.fetch_text(url)
web.fetch_json(url)

# Search the web
search.web(query)
search.news(query)
```

### User Interaction (`ui`)

```python
from anya.libs import ui

ui.ask("Choose:", ["option1", "option2"])  # Pick from options
ui.confirm("Continue?")                     # Yes/no
ui.input("Enter value:")                    # Text input
```

### Buffer Modification (`buffer`)

```python
from anya.libs import buffer

# Replace buffer contents (during :Anya do)
buffer.modify("def new_function():\n    pass")

# Append to buffer
buffer.modify("# End of file", mode="append")

# Prepend to buffer
buffer.modify("# Header", mode="prepend")
```

**Note:** The `buffer` lib only works inside `execute()` calls when a current buffer
context is available (e.g., during `:Anya do` operations). Use `fs.write_file()` for
writing to arbitrary files.

### Background Jobs (`background`)

Start long-running processes by setting the `execute` tool call's `background=True`, not by shell-detaching inside the command. Do not append `&` or use `nohup`, `disown`, `setsid`, `tmux`, or `screen`; those bypass Anya's job tracking and make logs/status/stop unreliable.

```python
from anya.libs import background

# List all background jobs
jobs = background.list_jobs()

# Get specific job info
job = background.get_job("process_id")

# Tail last N lines of output
logs = background.tail_logs("process_id", lines=50)

# Read log range
logs = background.read_logs("process_id", start=0, end=100)

# Check if job is still running
running = background.is_running("process_id")

# Stop a running job
result = background.stop_job("process_id")

# Wait for job completion (with timeout)
final_status = background.wait_for_job("process_id", timeout_seconds=30)
```



---

## Marker System

- **Markers are HTML comments**, embedded and concealed using Vim/Lua syntax, enabling lossless and minimally intrusive tracking
- **Message markers:** Boundaries for assistant/user/tool messages with all metadata
- **Edit/tool markers:** Indicate pending, applied, failed, etc. states for edits or tool blocks, enabling safe edit gating/undo
- **Markers govern buffer folding**, extmark placement, and region conceal; e.g., edits are shown as folded blocks until user takes an action
- **Markers always survive restarts and are recoverable from buffer or history**

---

## Environment & Dependencies

**Python:** 3.11+

**Required packages**:
  - `pynvim`, `openai`, `openai-agents`, `hashids`, `pyzmq`, `cbor2`, `html2text`, `anthropic`, `tenacity`, `playwright`
**Optional for advanced features**:
  - `snacks.nvim`, `stylua`, `ruff`, `luacheck`
  - Extra MCP tools: see [agent documentation](#the-agent-system--extensibility)

**Key Environment Variables**
| Variable             | Default    | Purpose                      |
|----------------------|------------|------------------------------|
| `OPENAI_API_KEY`     | (required) | LLM access (not needed for Copilot) |
| `ANYA_MODEL`         | gpt-4.1    | Default LLM                  |
| `ANYA_API_KEY`       | (unset)    | Override API key (for OpenRouter, etc.) |
| `ANYA_API_BASE`      | (unset)    | Custom API endpoint (falls back to `OPENAI_API_BASE`) |
| `ANYA_API_TYPE`      | responses  | API type: "responses" (default), "chat_completions", "anthropic", or "copilot" |
| `ANYA_OPENAI_API_TYPE` | (unset)  | Legacy/fallback alias for API type |
| `ANYA_THINKING_BUDGET`| (unset)   | Reasoning effort for model   |
| `ANYA_DISABLE_MCP`   | "0"        | Disable MCP agent/tools      |
| `ANYA_CONTEXT_WINDOW` | (unset)   | Override detected model context window for token usage calculations |
| `OPENAI_API_BASE`    | (unset)    | OpenAI-compatible API base fallback |
| `ANTHROPIC_API_KEY`  | (unset)    | Anthropic key fallback when `ANYA_API_TYPE=anthropic` |

### Client-Side Settings
Anya supports **per-client settings** that override the daemon's environment. This means you can
run multiple Neovim instances with different models/providers, all talking to the same daemon.

When you set environment variables *before* starting Neovim, those settings are passed to the
daemon and used for that specific session. The daemon caches agents by settings hash, so different
clients can use different configurations without conflict.

Example workflow:
```bash
# Terminal 1: Use OpenAI
export ANYA_MODEL=gpt-4.1
nvim +Anya

# Terminal 2: Use OpenRouter Claude
export ANYA_API_TYPE=chat_completions
export ANYA_API_KEY="$OPENROUTER_API_KEY"
export ANYA_API_BASE="https://openrouter.ai/api/v1"
export ANYA_MODEL="anthropic/claude-opus-4.5"
nvim +Anya
```

### OpenRouter Support
Anya supports OpenRouter models out of the box. When you set `ANYA_MODEL` to an OpenRouter model
(e.g., `anthropic/claude-sonnet-4`, `openai/gpt-4o`, `deepseek/deepseek-r1`), Anya automatically:
- Detects the model uses `/` or `:` in the name (OpenRouter convention)
- Creates a custom model provider with the OpenRouter API endpoint
- Routes requests through `https://openrouter.ai/api/v1`

To use OpenRouter:
1. Set your OpenRouter API key: `export ANYA_API_KEY=sk-or-...` (or use `OPENROUTER_API_KEY`)
2. Set your model: `export ANYA_MODEL=anthropic/claude-sonnet-4`
3. Set API type for non-OpenAI providers: `export ANYA_API_TYPE=chat_completions`

You can also use a custom API base for other OpenAI-compatible providers:
```bash
export ANYA_API_TYPE=chat_completions
export ANYA_API_BASE=https://your-custom-endpoint.com/v1
export ANYA_API_KEY=your-api-key
export ANYA_MODEL=your-model-name
```

### GitHub Copilot Support
Anya supports GitHub Copilot as a provider, allowing you to use Copilot's models without
an OpenAI API key. This uses the same models available in VS Code's Copilot Chat.

**Setup:**
1. Login once with the device flow:
   ```
   :Anya copilot login
   ```
   This will prompt you to visit `https://github.com/login/device` and enter a code.

2. Set environment variables:
   ```bash
   export ANYA_API_TYPE=copilot
   export ANYA_MODEL=gpt-4o  # or claude-3.5-sonnet, o3-mini, etc.
   ```

3. Restart Neovim and use Anya normally.

**Commands:**
| Command | Description |
|---------|-------------|
| `:Anya copilot login` | Run the OAuth device flow to authenticate |
| `:Anya copilot logout` | Remove stored authentication tokens |
| `:Anya copilot status` | Show current authentication status |
| `:Anya copilot models` | List available Copilot models |

**Available Models:**
- `gpt-4o`, `gpt-4.1`, `gpt-4.1-mini`
- `claude-3.5-sonnet`, `claude-3.7-sonnet`
- `o3-mini`, `o1`
- (and more depending on your subscription tier)

**How it works:**
- Authentication uses a two-stage OAuth device flow (no web server needed)
- A long-lived GitHub token is stored at `~/.local/share/anya/copilot_token`
- Short-lived Copilot API tokens (~30 min) are cached and auto-refreshed
- Copilot uses the OpenAI chat completions format (not the responses API)

---

## Agent Skills

Anya supports Claude Code-compatible **Agent Skills** — modular, filesystem-based capabilities that extend the agent with domain-specific instructions, workflows, and resources.

### Directory Layout

Skills are directories containing a `SKILL.md` file. Anya scans **four locations** in
priority order (later overrides earlier for the same skill name):

| Location | Scope | Ecosystem |
|---|---|---|
| `~/.claude/skills/<name>/` | Global | Claude Code |
| `~/.agents/skills/<name>/` | Global | Universal (skills.sh) |
| `.claude/skills/<name>/` | Project-local | Claude Code |
| `.agents/skills/<name>/` | Project-local | Universal (skills.sh) |

Project-local skills take precedence over global ones. Within the same scope,
`.agents/` takes precedence over `.claude/` — so you can use skills installed via
`npx skills add` (which writes to `~/.agents/skills/`) alongside hand-crafted
Claude Code skills without conflicts.

### Creating a Skill

Every skill requires a `SKILL.md` with YAML frontmatter:

```markdown
---
name: my-skill
description: What this skill does and when to use it. Use when the user asks about X.
---

# My Skill

## Instructions

Step-by-step guidance for the agent.

## Examples

Concrete examples of using this skill.
```

**`name`** rules:
- Maximum 64 characters
- Only lowercase letters, numbers, and hyphens
- Cannot contain "anthropic" or "claude"

**`description`** rules:
- Non-empty, maximum 1024 characters
- Should describe both *what* it does and *when* to use it

### Skill Content Levels

| Level | Content | When Loaded |
|---|---|---|
| **1: Metadata** | `name` + `description` from frontmatter | Always (injected into system prompt at startup) |
| **2: Instructions** | Full `SKILL.md` body | When agent reads the file on demand |
| **3: Resources** | Bundled files, scripts, templates | When referenced from SKILL.md |

The agent sees only the name/description at startup (~100 tokens per skill). When a request matches, it reads the full `SKILL.md` using `execute`. Additional files and scripts referenced in `SKILL.md` are loaded or executed as needed.

### Example Skill with Bundled Resources

```text
.claude/skills/deploy/
├── SKILL.md          # main instructions
├── CHECKLIST.md      # pre-deploy checklist (read on demand)
└── scripts/
    └── validate.sh   # validation script (executed, output only in context)
```

### How the Agent Uses Skills

1. At startup, skills metadata is injected into the system prompt automatically
2. When a user request matches a skill's description, the agent reads `SKILL.md`
3. The agent follows the instructions, reading bundled files or running scripts as needed
4. Scripts are run via `shell.run()` — only their output enters context, not source code

### Cache Invalidation

The agent is recreated whenever skills change on disk (files added, removed, or `SKILL.md` modified). No manual restart required.


---

## Best Practices

- All commands under `:Anya` (no command pollution!)
- Always update remote plugins after Python changes: `:UpdateRemotePlugins`, then restart Neovim
- Never write buffer output if streaming/queue not drained! (otherwise you risk marker/out-of-order bugs)
- Use marker and fold helpers (never fudge manual edits into the chat buffer)
- For extensibility: keep tool signatures explicit, use marker helpers/IDs, and test round-tripping with restarts and history reload
- Develop in split/test Neovim windows, especially for buffer/marker debugging
- Keymaps for Anya chat/prompt buffers may also be enforced from `rplugin/python3/anya/buffers.py` during window/buffer setup, so when changing buffer-local mappings (especially navigation like `<C-w>`), update both the ftplugin files and `buffers.py` if needed to avoid one side being overridden or behaving inconsistently.

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
