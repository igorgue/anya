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

* Primary implementation in **Python** (agent runtime + plugin via `pynvim`) is preferred. Optionally: plugin UI in Lua and backend agent in Python.
* Must run entirely in the user's environment (no remote server required).
* Plugin should have access to open Neovim buffers, file paths, working tree, and be able to preview & apply patches (with explicit user approval).
* Streaming responses: show tokens as the agent produces them in the content window.
* Safety: require explicit user confirmation before any file writes or shell execution.

---

## 2. High-level architecture

```
┌──────────────────────┐       IPC (WebSocket / HTTP + SSE)      ┌─────────────────────┐
│  Neovim Plugin (UI)  │ <-------------------------------------> │ Python Agent Server │
│  - UI buffers        │                                         │  - OpenAI Agents SDK│
│  - Input handling    │                                         │  - Tools (read/write)│
│  - Apply patches UI  │                                         │  - Tool adapters     │
└──────────────────────┘                                         └─────────────────────┘
```

**Components**

1. **Agent Server (Python)**

   * Uses OpenAI Agents SDK to define agent(s), tools, memory, traces.
   * Exposes an async API (prefer WebSocket for streaming; fallback HTTP + SSE).
   * Runs as a local process in a venv/pyenv environment.

2. **Neovim Plugin (pynvim or Lua + RPC)**

   * Starts/stops/monitors the agent server (optionally).
   * Manages UI buffers: content buffer (markdown) and prompt buffer.
   * Streams agent output into content buffer; renders markdown-friendly features (code blocks, patches).
   * Presents actions (Preview / Apply / Reject / Run tests).

3. **IPC Layer**

   * WebSocket recommended (bidirectional, streaming). Protocol should be JSON messages (see schema below).

4. **Tooling & Sandbox**

   * Tools like `read_file`, `list_files`, `search_repo`, `apply_patch`, `run_tests` implemented in Python server and exposed to agent.
   * All destructive tools require explicit confirmation from the Neovim side.

---

## 3. Data formats & protocol

**JSON message model (WebSocket)**

* Client → Server (Neovim → Agent)

```json
{
  "type": "ask",
  "id": "<uuid>",
  "prompt": "Fix bug in function foo",
  "context": {
    "cwd": "/home/user/repo",
    "files": {
      "src/foo.py": "def foo():\n  pass\n"
    },
    "selection": {
      "path": "src/foo.py",
      "start": [10,0],
      "end": [20,0]
    },
    "metadata": { "branch": "main" }
  },
  "options": {
    "dry_run": true,
    "max_steps": 5
  }
}
```

* Server → Client (streaming tokens + events)

```json
{ "type":"stream","id":"<uuid>","delta":"Applying patch..."}
{ "type":"stream","id":"<uuid>","delta":"@@ -1,3 +1,5 @@\n-foo\n+foo_fixed\n"}
{ "type":"done","id":"<uuid>","result":{"patch":"--- ...","explanation":"I fixed X by...","tools_used":["search_repo"]}}
{ "type":"tool_call","id":"<uuid>","tool":"read_file","args":{ "path":"src/bar.py" } }
{ "type":"tool_result","id":"<uuid>","tool":"read_file","result":"...file contents..."}
{ "type":"error","id":"<uuid>","error":"rate limit"}
```

**Patch format**

* Use unified diff (text) or a JSON patch list like:

```json
[{"op":"replace","path":"src/foo.py","start":10,"end":20,"text":"new code"}]
```

* Prefer unified diff for human-readable preview and reuse of `git apply` workflow.

---

## 4. UI design & Neovim internals

**Window layout**

* Fullscreen/tab layout with two windows side-by-side or stacked:

  * Left (or top): **Content buffer** (markdown), `ft=markdown`.
  * Right (or bottom): **Prompt buffer** (single-line or `ft=prompt` custom).
* Use either a **tabpage** dedicated to the agent or a floating fullscreen layout (user chooses).

**Content buffer features**

* Append new messages as markdown blocks:

  * User messages: `> **User:** ...`
  * Agent messages: code-blocks for diffs, fenced code blocks for snippets, bullet lists for steps.
* Stream tokens by appending to the latest agent message and invoking incremental buffer writes.
* Add **virtual text** annotations for explanations near code blocks or inline suggestions.
* Add commands at top/bottom for actions: `[Preview Patch] [Apply] [Run Tests] [Open File]`.

**Prompt buffer features**

* Provide `Tab` completion with recent prompts/history.
* Pressing `Enter` sends to agent; `Ctrl-C` cancels in-flight requests.
* Provide command palette mapping: `:AgentAsk` / `:AgentOpen` / `:AgentApply`.

**Rendering Markdown**

* Use native Neovim buffer with `filetype=markdown`: it supports folds and syntax highlighting.
* For richer rendering (HTML preview), optionally integrate with a markdown preview plugin (e.g., `glow`, `markdown-preview.nvim`) — keep functionality working without external dependency.
* Keep the content buffer text-first (so operations like search, copy, patch apply are simple).

**Diff preview & apply**

* When agent suggests a patch:

  * Insert a new buffer with `ft=diff` containing the unified diff.
  * Provide mappings `:AgentPreviewApply` or virtual buttons to either apply or reject.
  * Implementation of apply:

    * Use `git apply --index` if repo is git and `auto_stash` is desired, or
    * Apply via Neovim buffer edits (preferred for finer control). Use patch parsing library for this or implement a small parser.
  * Always show "dry-run" output and require confirmation.

---

## 5. Agent server — components & tools

**Core agent server responsibilities**

* Instantiate agents and tools via the OpenAI Agents SDK.
* Provide runtime for multi-step planning and tool orchestration.
* Stream outputs back to client.
* Keep persistent traces/logs for debug.

**Suggested tools (Python implementations)**

* `read_file(path)` → returns file contents
* `list_files(glob)` → returns list of repo files
* `search_repo(query, path)` → ripgrep or Python search
* `apply_patch(patch_str)` → *only apply if Neovim confirmed* — perform apply or return ops
* `run_tests(cmd)` → execute tests and return results (requires explicit confirmation)
* `run_shell(cmd)` → **dangerous**; default disabled, require permission
* `get_buffer(path)` → return in-memory buffer contents from Neovim (if plugin pushes it)
* `open_file_in_editor(path, pos)` → request plugin to open file (RPC from server to Neovim)

**Guardrails**

* Agent can *propose* `apply_patch` but server must wait for explicit client approval before calling the tool that writes to disk.
* Rate limiting and token budget per session.
* Logs & traces must be saved to `~/.local/share/your-plugin/trace/`.

---

## 6. Implementation plan (Milestones)

### Milestone 0 — Repo skeleton (1 day)

Create project scaffold.

```
neovim-agent/
├── agent/
│   ├── server.py
│   ├── agents/
│   │   └── default_agent.py
│   └── tools/
│       └── file_tools.py
├── plugin/
│   ├── rplugin/python3/agent_nvim.py  # pynvim entrypoint
│   └── lua/agent.lua                  # optional lua shim
├── docs/
│   └── plan.md
├── tests/
├── pyproject.toml
└── README.md
```

Commands:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install fastapi uvicorn aiohttp pynvim openai-agents-sdk  # placeholder names
```

### Milestone 1 — Agent server skeleton with WebSocket streaming (2 days)

* Implement `server.py` exposing:

  * `/ws` endpoint accepting client connections.
  * Basic handler that echoes prompts and streams made-up tokens (for testing).
* Implement simple `ask` endpoint to accept prompt & context; return a simulated patch and explanation.

Minimal FastAPI + WebSocket sketch:

````python
# server.py sketch
from fastapi import FastAPI, WebSocket
import uvicorn, asyncio
app = FastAPI()

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    while True:
        msg = await ws.receive_json()
        if msg["type"] == "ask":
            uid = msg["id"]
            # stream tokens
            for chunk in ["hello", " world", "\n```diff\n..."]:
                await ws.send_json({"type":"stream","id":uid,"delta":chunk})
                await asyncio.sleep(0.05)
            await ws.send_json({"type":"done","id":uid,"result":{...}})
````

**Goal:** be able to connect from Neovim and see streaming tokens appearing.

### Milestone 2 — Neovim plugin skeleton with UI (2 days)

* Implement `pynvim` plugin:

  * Commands: `:AgentOpen`, `:AgentAsk`, `:AgentCancel`
  * Create tab or dedicated layout with two buffers (markdown content + prompt).
  * WebSocket client inside plugin using `aiohttp` or a background Python thread with `websockets` lib.
* Implement streaming append to content buffer and basic prompt send.

Key points:

* Make plugin async/non-blocking. Use a background asyncio loop if running in Python plugin.
* Safely handle connection errors and reconnections.

### Milestone 3 — Patch preview & apply flow (2-3 days)

* Agent returns unified diff.
* Plugin displays `ft=diff` buffer and provides `:AgentApply` mapping.
* Implement safe apply:

  * Parse unified diff and apply to buffers via Neovim API.
  * Optionally run `git apply` on disk copy and open changed buffers.

### Milestone 4 — Integrate OpenAI Agents SDK + real tools (3-5 days)

* Replace simulated agent with a real agent from the Agents SDK.
* Implement tools (read_file, search_repo) on server and register with agent.
* Add memory/tracing hooks.

### Milestone 5 — UX polish, config, and guardrails (2-4 days)

* Add conversation history per project.
* Add settings: which tools enabled, token budget, model selection, local LLM adapters.
* Implement user confirmations and secure defaults.
* Add tests and CI.

### Milestone 6 — Packaging & docs (1-2 days)

* Provide install instructions, plugin manager declarations, and a `systemd` (or platform) unit to auto-start agent server (optional).
* Add `Makefile` or `tox` tasks.

*Total rough estimate*: 2–3 weeks for a good MVP by one developer. (Milestones ordered for incremental progress; you can stop after Milestone 2 and still get useful results.)

---

## 7. Pyenv / Virtualenv & running in user's environment

**Recommendations**

* Use `pyenv`/`pyenv-virtualenv` or `venv` to isolate dependencies.
* The plugin should *not* hard-require plugin-managed Python — instead, provide a configuration:

  * `agent.python_path` — explicit path to the Python interpreter inside the venv.
  * If not configured, attempt to find a suitable interpreter: `~/.local/share/agent/.venv/bin/python` or fallback to system Python.
* Provide helper CLI to bootstrap a venv:

```bash
python -m venv ~/.local/share/neovim-agent/venv
~/.local/share/neovim-agent/venv/bin/pip install -r requirements.txt
```

* Optionally ship a small manager command `neovim-agent start` that ensures environment is ready and spins up server.

**Why separate process?**

* Keeps Neovim responsive.
* Allows heavy libraries (LLMs, tokenizers) to be installed without affecting Neovim's runtime.
* Easier to manage GPU/accelerator usage outside Neovim.

---

## 8. Lua-first frontend (optional)

If you prefer a Lua UI with Python backend:

* Build a thin Lua plugin for UI using native Neovim APIs.
* Use `luv` or `ffi`-based HTTP/WebSocket client or spawn a background job that proxies to Python (e.g., `curl --no-buffer` or `websocat`).
* Use the same protocol (WebSocket JSON) — Lua only manages windows and buffer updates, incoming messages routed from Python backend.

Tradeoffs:

* Lua UI is more responsive and integrates better with Neovim's ecosystem.
* Python UI is faster for development and allows direct `pynvim` access to Neovim API calls and seamless object passing.

---

## 9. Security & safety checklist

* Default to **no** destructive tools enabled. `apply_patch`, `run_shell` require explicit opt-in and confirmation per action.
* Keep all agent logs in user-only readable directories.
* Offer strict CORS / bind to `127.0.0.1` only.
* Ask for explicit user confirmation before running third-party code (tests, shell).
* Optionally allow enabling sandboxing through `firejail`, `nsjail`, or running tests inside an ephemeral container (advanced).

---

## 10. Observability & debugging

* Add debug flags: `--log-level DEBUG` and keep structured trace logs in JSON.
* Use an `agent.trace(id)` query to retrieve run details.
* Expose endpoints for health (`/healthz`) and for retrieving last N traces.

---

## 11. Testing

* Unit tests for:

  * Patch parsing & apply.
  * WebSocket protocol conformance.
  * Tool functions (read, list, search).
* Integration tests:

  * Start agent server in CI with a small model stub/mock server and test end-to-end prompt → diff → preview → apply (in dry-run).
* Manual test plan:

  * Basic question & answer (streaming).
  * Ask for a one-line fix and accept patch.
  * Ask to run tests (confirm the server asks for permission).

---

## 12. Example small agent/tool spec

**Agent description**:

```
You are a Neovim assistant. Use the provided repository files and user selection to propose patches. When you propose changes, output a unified diff and a short explanation. Do not run or apply patches without user consent. Use tools read_file and search_repo when needed.
```

**Tool signatures**:

* `read_file(path: str) -> str`
* `list_files(pattern: str) -> List[str]`
* `search_repo(q: str, max: int = 50) -> List[SearchResult]`
* `propose_patch(patch: str) -> None`  # returns patch but doesn't apply
* `apply_patch(patch: str) -> str`  # server side only, requires confirmation

---

## 13. Implementation snippets & helpers

**Start WebSocket server (Uvicorn + FastAPI)** — see Milestone 1 sketch above.

**Neovim: create tab with two windows**

```python
# inside pynvim plugin
self.nvim.command("tabnew")
self.nvim.command("vsplit")
# left buffer
left = self.nvim.api.create_buf(False, True)
left.options['filetype'] = 'markdown'
# right (prompt)
right = self.nvim.api.create_buf(False, True)
right.options['buftype'] = 'prompt'
# set mappings and enter prompt mode
```

**Appending streaming text safely**

```python
# Append lines incrementally:
buf = self.content_buf
current_text = buf[:]  # list of lines
# for chunk in stream:
lines = chunk.splitlines(keepends=False)
if len(lines)==0:
    continue
# update last line or append new lines
self.nvim.async_call(lambda: buf.append(lines))
```

**Applying unified diff using python `patch` approach**

* Use `python-unidiff` to parse diff and apply edits to buffers in memory, then write to disk.
* Alternatively, write to a temp file and call `git apply --whitespace=fix --index --reject`.

---

## 14. Integrating ideas from Avante & CodeCompanion

* **Conversation per-project**: persist conversation history in `.cc/` or `.neovim-agent/` folder.
* **Tools architecture**: CodeCompanion exposes many tool adapters — follow that pattern: register small, explicit tools and log their calls.
* **UI patterns**: follow CodeCompanion’s “preview before apply” and Avante’s inline edit hooks (diff buffer + apply).

---

## 15. Next steps (what I can produce now)

If you want, I can immediately produce:

* A minimal repository skeleton with `server.py` and `pynvim` plugin basic implementation (Milestone 0 & 1).
* Or produce the full `server.py` and `agent_nvim.py` code with working WebSocket streaming demo (Milestone 1 & 2).

Tell me which code artifact you want first and I’ll produce it (server, plugin, or both).
If you want me to proceed automatically, I’ll produce the minimal runnable skeleton next.
