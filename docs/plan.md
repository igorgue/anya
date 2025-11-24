# Neovim Agent Plugin — Detailed Plan

**Filename:** `plan.md`
**Author:** Igor’s Neovim Agent Plan
**Date:** 2025-11-23

---

## 1. Goals & constraints

**Primary goal**
Create an agentic Neovim plugin that provides an AI assistant using the OpenAI Agents Python SDK. The UI is full-screen (or tab/side) with two windows:

* **Content window** — rendered as Markdown, displays the conversation, agent reasoning, diffs/patches, code snippets, and streams agent output.
* **Prompt window** — single-line or small multi-line input area for user prompts.

**Constraints & preferences**

* **Architecture:** Pure **`pynvim`** plugin. The agent runs directly within Neovim's Python host process. No separate server or WebSocket.
* Must run entirely in the user's environment.
* Plugin should have access to open Neovim buffers, file paths, working tree, and be able to preview & apply patches (with explicit user approval).
* Streaming responses: show tokens as the agent produces them in the content window.
* Safety: require explicit user confirmation before any file writes or shell execution.

---

## 2. High-level architecture

```
┌──────────────────────────────────────────────┐
│           Neovim Process (nvim)              │
│                                              │
│  ┌────────────────────────────────────────┐  │
│  │      Python3 Host (pynvim)             │  │
│  │                                        │  │
│  │  ┌──────────────┐    ┌──────────────┐  │  │
│  │  │  UI Logic    │───▶│  Agent SDK   │  │  │
│  │  │ (Commands)   │◀───│ (Logic/LLM)  │  │  │
│  │  └──────────────┘    └──────────────┘  │  │
│  │         │                    │         │  │
│  └─────────┼────────────────────┼─────────┘  │
│            │                    │            │
└────────────┼────────────────────┼────────────┘
             ▼                    ▼
      Neovim API (RPC)       File System / Network
```

**Components**

1. **Neovim Plugin (`pynvim`)**

   * **Entry Point:** `rplugin/python3/agent_nvim.py`.
   * **UI Logic:** Handles commands (`:AgentAsk`), manages buffers, renders Markdown, and captures user input.
   * **Agent Runtime:** Instantiates the OpenAI Agents SDK classes directly.
   * **Async Handling:** Uses `asyncio` to handle LLM streaming without blocking the Neovim UI.
   * **Context Engine:** Runs `ripgrep` or other tools as subprocesses or library calls directly from the plugin.

2. **Tooling & Sandbox**

   * Tools (`read_file`, `search_repo`, etc.) are Python functions registered with the agent.
   * Destructive tools (`apply_patch`, `run_shell`) pause execution to request user confirmation via Neovim UI (e.g., `vim.ui.select` or a prompt buffer).

---

## 3. Concurrency & Non-blocking Strategy

To ensure the Neovim UI **never freezes** while the agent is thinking or streaming:

1.  **Asynchronous RPC:**
    *   All plugin commands (e.g., `:AgentAsk`) will be defined with `sync=False` (in pynvim decorators) or `{async=true}` (in Lua/Vimscript).
    *   This tells Neovim to **fire-and-forget** the command. Neovim continues processing user input immediately without waiting for Python to return.

2.  **Python `asyncio` Event Loop:**
    *   The Python host runs an `asyncio` event loop.
    *   The Agent SDK and Network I/O (OpenAI calls) will be `await`-ed. This yields control, allowing the Python host to process other messages (like a `:AgentCancel` command) while waiting for the LLM.

3.  **UI Updates via `nvim.async_call`:**
    *   When the agent has a token to stream, it schedules a UI update on the Neovim main loop using `nvim.async_call`.
    *   This ensures thread safety and keeps UI updates snappy without locking the editor.

---

## 4. Internal Data Flow

Since we are in the same process, we don't need a JSON wire protocol. We pass Python objects.

**Flow:**

1.  **User** types prompt in Prompt Buffer and hits Enter.
2.  **UI Logic** calls `agent.run(prompt, context)`.
3.  **Agent** (running in background task) yields events:
    *   `Token(text="Hello")` → UI appends "Hello" to Content Buffer.
    *   `ToolCall(name="read_file")` → UI shows "Reading file...".
    *   `ToolResult(output="...")` → Agent continues.
4.  **Agent** proposes a patch.
5.  **UI Logic** renders the diff and waits for `:AgentApply`.

---

## 4. UI design & Neovim internals

**Window layout**

* **Two-Window Split:** Unlike CodeCompanion (which uses a single buffer), we strictly separate:
    *   **Content buffer** (Left/Top): `ft=markdown`. Read-only view of the conversation, diffs, and tool outputs.
    *   **Prompt buffer** (Right/Bottom): `ft=prompt`. Small, dedicated input area.
*   *Rationale:* Prevents the "writing on a big document" feeling and keeps the context clean.

**Prompt buffer features**

* **Slash Commands:** `/help`, `/clear`, `/mode` (Inspired by CodeCompanion).
* **Variables / Mentions:** `@file`, `@buffer`, `@codebase`. (Similar to CodeCompanion's Variables).
* **Autocomplete:** CMP source for commands and variables.

**Diff preview & apply**

* **Strategy A (Classic):** Open a separate `ft=diff` buffer with the unified diff. User runs `:AgentApply`.
* **Strategy B (Inline Conflict Markers):** For small edits, inject **conflict markers** (`<<<<<<<`, `=======`, `>>>>>>>`) directly into the buffer. This leverages Neovim's native conflict highlighting.
* **Recommendation:** Start with Strategy A for safety, then implement Strategy B.

---

## 5. Agent components & tools

**Core responsibilities**

* Instantiate agents and tools via the OpenAI Agents SDK.
* **Project Instructions:** Load `./AGENTS.md` to seed agent context.
* **RAG Service:** A lightweight indexer to support `@codebase` queries.

**Suggested tools**

* `read_file(path)`
* `list_files(glob)`
* `search_repo(query, path)`
* `apply_patch(patch_str)` (Requires confirmation)
* `run_tests(cmd)` (Requires confirmation)
* `run_shell(cmd)` (Dangerous, disabled by default)

---

## 6. Dependency Management (Crucial)

Since we are running in `pynvim`, we share the environment with other Python plugins. To avoid conflicts and ensure our dependencies (OpenAI SDK, etc.) are available:

1.  **Virtualenv Strategy:**
    *   The plugin creates its own virtualenv at `~/.local/share/nvim-agent/venv`.
    *   On startup, the plugin **injects** this venv's `site-packages` into `sys.path`.
    *   This allows us to import our heavy dependencies without forcing the user to install them globally or in their main Neovim provider environment.

2.  **Bootstrap Command:**
    *   `:AgentInstall` -> Runs a shell script to create the venv and `pip install` requirements.

---

## 7. Implementation Steps

### Step 0 — Repo skeleton

* Project scaffold (`rplugin/python3/agent_nvim.py`).
* `pyproject.toml`, `requirements.txt`.
* `plugin/agent.vim` (Manifest generation or manual registration).

### Step 1 — Pynvim Setup & Dependency Injection

* Implement `:AgentInstall` to create venv.
* Implement `sys.path` injection in `agent_nvim.py`.
* Verify `import openai_agents` works inside Neovim.

### Step 2 — UI Skeleton

* Commands: `:AgentOpen`, `:AgentAsk`.
* Split layout (Content + Prompt).
* **Slash Commands:** Basic parsing for `/clear`, `/help`.

### Step 3 — Agent Loop & Streaming

* Implement the `async` agent loop.
* Stream text to the Content buffer using `nvim.async_call`.
* Handle user input from Prompt buffer.

### Step 4 — Tools & Patching

* Implement file tools (`read_file`, `search_repo`).
* Implement **Strategy A** (Diff buffer) for patching.
* Add confirmation flows for destructive tools.

### Step 5 — Context & RAG

* Implement `@codebase` mention handling.
* Basic RAG indexing/search.
* Load `./AGENTS.md`.

### Step 6 — UX Polish

* **Mentions UI:** Autocomplete for `@file`.
* **Inline Diffs:** Experiment with **Strategy B** (Conflict markers).

### Step 7 — Packaging & docs

* Installation guide.
* `checkhealth` provider.

---

## 8. Security & safety checklist

* **Confirmation:** All file writes and shell commands must be confirmed.
* **Sandboxing:** Explore running the agent in a container or restricted shell.
* **Logs:** Keep traces local.

---

## 9. Comparison & Inspiration

* **CodeCompanion.nvim:**
    * **Adapters:** We will adopt the "Adapter" pattern to allow easy switching between LLMs (OpenAI, Anthropic, Local).
    * **Variables:** We will implement `@file`, `@buffer` similar to CodeCompanion's variables.
    * **Slash Commands:** We will use the `/cmd` syntax for actions.
    * **Difference:** We strictly enforce a **Two-Window Layout** (Content + Prompt) to keep the chat history clean and separate from the input area, whereas CodeCompanion typically uses a single buffer.
