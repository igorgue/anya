# agent.nvim

An agentic Neovim plugin powered by the OpenAI Agents SDK.

## Features

- **Chat Interface**: Split-window layout with streaming responses.
- **Context Awareness**: Reference files using `@filename` (with autocompletion).
- **Tools**: The agent can read files, list directories, and search the repository.
- **Tool Folding**: Tool calls and results are automatically folded to reduce clutter. Use `za` to toggle folds.
- **Patching**: The agent can propose patches, which you can review and apply.
- **Project Instructions**: Customize the agent's behavior with `AGENTS.md`.

## Installation

### Prerequisites

- Neovim >= 0.9.0
- Python 3.8+
- `pynvim` (installed globally or in your Neovim provider environment)

### Using [lazy.nvim](https://github.com/folke/lazy.nvim)

```lua
{
    "igor/agent.nvim",
    build = ":UpdateRemotePlugins",
    cmd = "AgentOpen",
}
```

### Post-Installation

1.  Start Neovim.
2.  Run `:UpdateRemotePlugins` (if not done automatically).
3.  Run `:AgentInstall` to set up the plugin's virtual environment and dependencies.
4.  Restart Neovim.

## Configuration

Set your OpenAI API key in your environment variables:

```bash
export OPENAI_API_KEY="sk-..."
export AGENT_API_KEY=$OPENAI_API_KEY
```

### Optional Configuration

You can customize the base URL and model using environment variables:

```bash
# Use a custom API endpoint (e.g., OpenAI-compatible server)
export AGENT_BASE_URL="https://your-api-endpoint.com/v1"

# Specify a model (defaults to OpenAI's default)
export AGENT_MODEL="gpt-4-turbo-preview"

# Custom model for context compaction (defaults to AGENT_MODEL)
export AGENT_COMPACT_MODEL="gpt-4o-mini"

# Disable tracing for custom providers
export AGENT_DISABLE_TRACING=1
```

**Note:** The base URL works with both the Chat Completions API and the Responses API. Streaming is supported on the Chat Completions API.

## Usage

1.  Run `:AgentOpen` to open the chat interface.
2.  Type your message in the bottom prompt window.
3.  Press `Enter` to send.

### Mentions

Type `@` followed by a filename to include its content in your prompt. Use `<C-x><C-u>` (User Completion) to autocomplete file paths.

### Tool Folding

When the agent uses tools (like reading files or searching the repository), the tool calls and their results are displayed in the chat but automatically folded to keep the interface clean. You can:

- Press `za` to toggle a fold open/closed
- Press `zo` to open a fold
- Press `zc` to close a fold
- Press `zR` to open all folds
- Press `zM` to close all folds

### Patching

If you ask the agent to modify code, it may propose a patch. The patch will be shown in a separate `AgentDiff` buffer. Review the changes and run `:AgentApply` to apply them to your files.

### Context Compaction

When conversations become long, you can use the `/compact` command to intelligently summarize and reduce token usage while preserving essential context:

```bash
# Basic compaction with automatic settings
/compact

# Control compaction intensity
/compact aggressively    # Heavy reduction (~30%)
/compact lightly        # Gentle reduction (~85%)

# Target specific token count
/compact --tokens=2000

# Focus on specific topics
/compact focus on authentication flow
/compact remove debugging sessions

# Complex natural language instructions
/compact preserve discussions about database design, API contracts, and user authentication. Remove the CSS styling conversations.
```

**Features:**
- **Smart Targeting**: Automatically infers token targets from natural language
- **Preview Interface**: Shows before/after comparison with statistics
- **Selective Preservation**: Maintains active tasks, decisions, and file references
- **User Control**: Edit summary before accepting, cancel anytime

**Environment Variables:**
- `AGENT_COMPACT_MODEL`: Custom model for compaction (defaults to `AGENT_MODEL`)

### Custom Instructions

Create an `AGENTS.md` file in your project root to provide specific instructions to the agent (e.g., coding style, architecture overview).
