# AGENTS.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Project Overview

agent.nvim is a Neovim plugin that integrates OpenAI's Agents SDK, providing an AI assistant with file system access, code editing capabilities, and context awareness. It's a Python remote plugin that communicates with Neovim via pynvim.

## Architecture

### Key Components

**Python Remote Plugin** (`rplugin/python3/agent_nvim/plugin.py`)
- Main plugin logic using `@pynvim.plugin` decorator
- Manages virtual environment at `~/.local/share/agent.nvim/venv`
- Handles async operations via asyncio for agent execution
- Implements tools: file reading, directory listing, repo search, and code editing
- Includes token tracking and budget management to prevent context overflow

**Vim Layer** (`plugin/agent.vim`)
- Bootstrap script for `:AgentInstall` command
- Provides installation without requiring remote plugin to be loaded first
- Hardcoded path to `scripts/install.py`

**Buffer Management** (`rplugin/python3/agent_nvim/buffers.py`)
- `AgentContent`: Markdown buffer for chat history and responses
- `AgentPrompt`: Special filetype buffer for user input with custom completion
- Streaming responses with Lua animation for smooth typing effect

**File Type Configuration**
- `ftplugin/agent-prompt.vim`: Maps Enter to submit, sets completefunc, toolbar keymaps
- `ftplugin/agent-prompt.lua`: Placeholder display and toolbar initialization
- `ftplugin/agent-content.vim`: Configures wrapping, fold settings, and smart autoscroll
- `syntax/agent-prompt.vim`: Highlights slash commands and `@` mentions

**Configuration Management** (`rplugin/python3/agent_nvim/config.py`)
- Persists agent and mode settings to `~/.config/agent.nvim/config.json`
- Integrates with Lua toolbar for state synchronization
- Provides get/set interface for configuration values

**Toolbar UI** (`lua/agent_nvim/toolbar.lua`)
- Shows current agent and mode in bottom right of prompt buffer
- Supports toggling between main agents (CODER, PLAN)
- Supports toggling between modes (ASK, YOLO)
- Picker for specialized agents (REVIEWER, VERIFIER, COMPACT)
- Color-coded indicators with customizable highlights

**Folding System** (`lua/agent_nvim/folds.lua`)
- Manages manual folds for tool calls and results
- Tool calls and results are automatically folded when displayed
- Use `za` to toggle folds, `zo` to open, `zc` to close
- Fold summaries show tool name or result indicator when collapsed

**MCP Support** (`rplugin/python3/agent_nvim/mcp.py`)
- Manages Model Context Protocol servers
- Supports stdio, HTTP, SSE, and hosted MCP servers
- Configured via `~/.config/agent.nvim/mcp/servers.json`

### Data Flow

1. User opens interface with `:AgentOpen` → creates split layout with content/prompt buffers
2. User types in prompt buffer → mentions (`@filename`) trigger file path completion
3. Enter key → `AgentSubmit` command resolves mentions and sends to agent
4. Agent execution → runs in executor thread to avoid blocking Neovim
5. Agent uses tools → `read_file`, `list_files`, `search_repo`, `edit`
6. Responses stream → appended to content buffer via Lua animation
7. Code edits → applied via SEARCH/REPLACE blocks

### Project Instructions

The plugin looks for `AGENTS.md` or `.agent/instructions.md` in the project root to load custom instructions that are prepended to the agent's system prompt.

## Development Commands

### Installation & Setup
```bash
# Install dependencies in isolated venv
:AgentInstall

# Or run installation script directly
python3 scripts/install.py

# Update remote plugins after code changes
:UpdateRemotePlugins
```

### Testing
```bash
# Verify dependencies are importable
:AgentTestImport
```

### Running
```bash
# Open agent interface
:AgentOpen

# Submit a prompt (mapped to Enter in prompt buffer)
:AgentSubmit

# Cancel a running request (mapped to Ctrl+C)
:AgentCancel

# Sync configuration from disk
:AgentSyncConfig
```

### Toolbar Keymaps (in prompt buffer)
- `<localleader>a` - Toggle main agents (CODER ↔ PLAN)
- `<localleader>y` - Toggle mode (ASK ↔ YOLO)
- `<localleader>A` - Open picker for specialized agents (REVIEWER, VERIFIER, COMPACT)

## Dependencies

- Python 3.8+
- `pynvim` - Neovim Python client
- `openai` - OpenAI Python SDK
- `openai-agents` - OpenAI Agents SDK

Dependencies are installed to `~/.local/share/agent.nvim/venv` and injected into sys.path at plugin initialization.

## Environment Variables

- `OPENAI_API_KEY` - Required for agent functionality
- `AGENT_MODEL` - Model to use (default: gpt-5.1)
- `AGENT_BASE_URL` - Custom API endpoint (optional)
- `AGENT_API_KEY` - Alternative API key (optional)
- `AGENT_API_TYPE` - API type: 'responses' or 'chat_completions' (default: responses)
- `AGENT_DISABLE_TRACING` - Disable tracing for custom providers (default: 1)
- `AGENT_MAX_READ_BYTES` - Maximum bytes to read from files (default: 64000)
- `AGENT_CONTEXT_WINDOW` - Override context window size (optional)
- `AGENT_COMPACT_MODEL` - Custom model for CompactAgent (default: same as AGENT_MODEL)
- `AGENT_YOLO` - Enable YOLO mode: auto-apply patches and exec commands without user approval (set to 1, true, or yes)
- `AGENT_MAX_TURNS` - Maximum number of agent turns before forcing a response (default: 1000, effectively unlimited)

## Special Considerations

### Exec Command Permissions
- Shell commands require user approval before execution via `vim.ui.select`
- Three options: "Run" (one-time), "Do not run" (deny), "Allow always" (permanent)
- Allowed commands are stored in `~/.config/agent.nvim/exec_allow_list.txt`
- YOLO mode (`AGENT_YOLO=1`) bypasses all confirmation prompts

### Path Management
- The plugin dynamically discovers and injects venv site-packages into sys.path
- File paths in tools are resolved relative to Neovim's current working directory
- The `plugin/agent.vim` has a hardcoded path that may need updating if file structure changes

### Buffer Lifecycle
- Buffers are reused across `:AgentOpen` invocations
- Buffer validity is checked before operations
- Content buffer starts with a welcome message if empty

### Async Execution
- Agent runs in executor thread to prevent blocking
- Uses `nvim.async_call` for all Neovim API calls from async context
- Logging goes to `~/.local/state/nvim/agent.nvim.log`

### Token Management
- Tracks token usage across requests to prevent context overflow
- Implements tool budget system to limit file reading/searching
- Displays token usage in real-time with color-coded highlighting
- Forces early response when approaching context limits

### Patch Application
- Creates temporary file for patch content
- Uses `git apply --ignore-space-change --ignore-whitespace`
- Runs `checktime` after applying to reload modified buffers

### Completion
- Custom `AgentComplete` function for file path completion after `@`
- Triggered with `<C-x><C-u>` in prompt buffer
- Searches recursively from cwd, excluding `.git` directories
- Limited to 50 results

### Smart Autoscroll
- Content buffer automatically scrolls to bottom while LLM is responding (when user is at bottom)
- Scrolling up during response disables autoscroll, allowing user to read earlier content
- Scrolling back to the bottom during response re-enables autoscroll
- Submitting a new prompt resets autoscroll to enabled
- Implemented via `WinScrolled`/`CursorMoved` autocmds with `b:agent_autoscroll_enabled` flag
- Both Python append operations and Lua streaming respect the autoscroll state

### Conversation History Preservation
- **Error/Cancellation Handling**: Failed requests and cancelled responses are preserved in conversation history as error messages, allowing users to continue with context
  - Captures only the partial LLM response (not buffer headers, welcome message, or user prompts)
  - Tracks where agent response starts to extract clean output
  - Preserves error messages with full context for debugging
- **Tool Context Preservation**: All tool usage (file reads, searches, file listings) is automatically recorded and added to conversation history as system messages
- **Continuous Context**: Users can use `continue` or add comments to drive the conversation forward without losing prior context
- **Implementation**: 
  - `tool_tracker.py` - Records all tool calls and results
  - `agent_runner.py` - Captures partial LLM output on cancellation or error, tracks response start line
  - `plugin.py` - Integrates tool tracking into tool wrappers and agent lifecycle
- **Benefits**: Enables recovery from transient failures, allows iterative refinement, and preserves complex exploration context

## Codebase Context

### Important Files
- `rplugin/python3/agent_nvim/` - Modularized plugin Python code
  - `plugin.py` - Main plugin class
  - `buffers.py` - Buffer management and UI
  - `tool_events.py` - Tool call display and formatting with folding
  - `agent_runner.py` - Agent execution logic with streaming
  - `tools.py` - Tool implementations with budget tracking
  - `utils.py` - Utility functions
  - `token_tracker.py` - Token usage tracking
  - `tool_budget.py` - Tool budget management
  - `tool_tracker.py` - Conversation history preservation tracking for tool usage
  - `mcp.py` - MCP server management
- `plugin/agent.vim` - Bootstrap installation command
- `ftplugin/agent-prompt.vim` - Prompt buffer configuration
- `ftplugin/agent-content.vim` - Content buffer configuration with folding
- `syntax/agent-prompt.vim` - Syntax highlighting for mentions and commands
- `lua/agent_nvim/folds.lua` - Fold management for tool calls/results
- `scripts/install.py` - Standalone installation script
- `doc/agent.txt` - Vim help documentation

### Directories to Note
- `avante.nvim/` and `codecompanion.nvim/` - Appear to be separate plugin repositories in the workspace (not part of agent.nvim)
- `docs/` - Additional documentation directory including folding implementation details

## Slash Commands

- `/clear` - Clear chat history
- `/cancel` - Cancel current request
- `/file` - Open Snacks file picker to select and add multiple files to the prompt as `@` references
- `/compact [instructions]` - Compact conversation context to reduce token usage
  Examples:
  - `/compact` - Compact with automatic settings
  - `/compact aggressively` - Heavy compaction (~30% of original)
  - `/compact lightly` - Light compaction (~85% of original)
  - `/compact focus on authentication` - Focus on specific topics
  - `/compact --tokens=2000` - Target specific token count
- `/help` - Show help message

### `/file` Command Details

The `/file` slash command provides an interactive file picker to select one or more files and add them to the prompt as `@` references:

1. Type `/file` in the prompt buffer and press Enter
2. Snacks file picker opens showing files from the project root
3. Use configured multi-select keybinding (default Ctrl+Space) to select multiple files
4. Press Enter to confirm and close the picker
5. Selected files are prepended to the prompt as space-separated `@` references
6. Files are highlighted with `Directory` highlighting for easy identification

**Examples:**
- `/file` followed by selecting `src/main.py` and `tests/test.py` results in prompt:
  ```
  @src/main.py @tests/test.py
  ```
- Adding context after files:
  ```
  @src/main.py @tests/test.py Here's my implementation
  ```

### `/compact` Command Details

The `/compact` slash command provides intelligent conversation context compaction using a specialized CompactAgent:

#### Basic Usage
- `/compact` - Automatic compaction with smart token targeting
- `/compact --tokens=2000` - Target specific token count

#### Natural Language Instructions
The command supports sophisticated natural language instructions for precise control:

**Intensity Control:**
- `/compact aggressively` - Heavy reduction (~30%)
- `/compact significantly` - Moderate reduction (~50%)
- `/compact lightly` - Gentle reduction (~85%)

**Content Filtering:**
- `/compact focus on authentication flow` - Preserve specific topics
- `/compact remove debugging sessions` - Remove specific content types
- `/compact keep only recent discussions` - Temporal filtering

**Complex Instructions:**
```
/compact preserve discussions about database design and API contracts, remove the CSS styling conversations
```

#### Features
- **Preview Interface**: Side-by-side comparison with statistics
- **Smart Targeting**: Automatic token inference from instructions
- **Selective Preservation**: Maintains active tasks, decisions, file references
- **User Control**: Edit before accepting, cancel anytime

#### Configuration
- `AGENT_COMPACT_MODEL`: Custom model for compaction (default: same as main agent)
- Requires OpenAI agents SDK and valid API key

### Tool Details

### File Reading

The `read_file` tool now supports intelligent line-range reading for large files.

**Syntax:**
- `@filename.py` - Read first 100 lines (default truncation for large files)
- `@filename.py@start-end` - Read entire file
- `@filename.py@32-234` - Read lines 32-234
- `@filename.py@start-100` - Read lines 1-100
- `@filename.py@280-end` - Read from line 280 to end

**Behavior:**
- Files with ≤100 lines: returned in full
- Files with >100 lines: first 100 lines returned with metadata showing total line count
- Metadata always includes:
  - Total line count and file size
  - Current line range being displayed
  - Suggestions for reading more (e.g., "@101-200" to continue)
- LLM receives full context to know file is too large and can request specific ranges
- This approach saves tokens by avoiding unnecessary full-file reads while keeping LLM informed

**Configuration:**
- Limited to 64,000 bytes by default (configurable via `AGENT_MAX_READ_BYTES`)
- Line-based truncation enables efficient exploration of large files

### Reading Multiple Files

The `read_many_files` tool reads multiple files in a single call, supporting the same line-range syntax:

**Usage:**
```
read_many_files(["file1.py", "file2.py", "file3.py@50-100", "file4.py@start-end"])
```

**Benefits:**
- Batch read multiple files with a single tool call
- Supports all range specifications for each file independently
- More token-efficient than multiple `read_file` calls
- Useful for reading related files together (e.g., interfaces and implementations)

### Directory Listing
- Recursive search excluding `.git` directories
- Limited to 100 files to prevent excessive output

### Repository Search
- Uses ripgrep with fallback to grep
- Limited to 2,000 characters of output
- Searches from current working directory

### Patch Application
- Creates diff buffer for review
- Uses `git apply` with whitespace tolerance
- Must be manually applied after review

### Context Compaction
- Uses specialized CompactAgent with custom system prompt
- Supports natural language instructions for selective compaction
- Provides preview interface with before/after statistics
- Automatically preserves active tasks, decisions, and file references
- Configurable model via `AGENT_COMPACT_MODEL`
- Integrates with existing conversation flow seamlessly

## Known Patterns

- All Neovim API calls from async contexts must use `nvim.async_call`
- String responses are split on newlines before writing to buffers
- File mentions are resolved before sending to agent but shown as-is to user
- Error messages are written to stderr via `nvim.err_write`
- Tool calls and results are automatically folded to reduce interface clutter
- Streaming is used for agent responses but instant append is used for tool output

## Coding Guidelines for Agents

- Do not add color emojis to the codebase. Use only monospace Unicode characters or text-based indicators where visual elements are needed.
