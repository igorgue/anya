# Tool Folding Implementation

## Current Status

**✅ IMPLEMENTED AND WORKING**

The folding system has been successfully implemented and is now fully functional for all tool calls and results. The async context issues have been resolved by using instant append for tool content instead of streaming.

**What works:**
- ✅ Folding infrastructure (Lua module, ftplugin, fold text)
- ✅ Folding for all tool calls via `display_tool_call()`
- ✅ Folding for all tool results via `display_tool_result()`
- ✅ Automatic fold creation with custom summaries
- ✅ Standard Neovim fold commands (`za`, `zo`, `zc`, `zR`, `zM`)

## Original Goal

Implement automatic folding for tool calls and their results in the agent.nvim chat interface, similar to codecompanion.nvim's approach. Tool calls and results would be automatically folded when displayed, showing only a summary line that can be expanded with `za`.

## Changes Made

### 1. Lua Folding Module (`lua/agent_nvim/folds.lua`)

Created a new Lua module to manage folds:

- **Fold Storage**: Maintains a mapping of buffer numbers to fold summaries
- **Fold Creation**: `create_fold(bufnr, start_row, end_row, summary)` - Creates a manual fold with a custom summary
- **Fold Text**: Custom `fold_text()` function that displays fold summaries (e.g., "🔧 Calling tool: tool_name")
- **Fold Cleanup**: `cleanup(bufnr)` - Cleans up fold data when buffers are closed

### 2. File Type Plugin (`ftplugin/agent-content.vim`)

Enhanced the agent-content filetype plugin to:

- Set `foldmethod=manual` for manual fold management
- Configure `foldtext` to use the custom Lua function
- Maintain existing line wrapping and display settings

### 3. Tool Event Handler (`rplugin/python3/agent_nvim/tool_events.py`)

Updated tool display functions to create folds automatically:

- **`display_tool_call()`**: Now tracks line positions and creates folds for tool call arguments
- **`display_tool_result()`**: Creates folds for tool results
- **`handle_tool_item()`**: Updated to create folds when processing tool items from the stream
- **`handle_tool_call_output()`**: Creates folds for tool output events

Each function now:
1. Records the buffer line count before appending content
2. Appends the tool call/result content
3. Records the buffer line count after appending
4. Creates a fold if multiple lines were added
5. Stores a summary for the fold (e.g., "🔧 Calling tool: read_file" or "✅ Tool result")

### 4. Buffer Manager (`rplugin/python3/agent_nvim/buffers.py`)

Added fold initialization when creating the content buffer:

- Calls `require('agent_nvim.folds').setup(bufnr)` after buffer creation
- Ensures manual folding is properly configured

### 5. Documentation Updates

- **WARP.md**: Added section on folding system, updated file list
- **README.md**: Added folding feature to features list and usage section

## How It Works

1. When a tool call is displayed, the content is appended to the buffer
2. After appending, the Python code calls the Lua `create_fold()` function
3. The fold is created using Neovim's manual fold commands (`:{start},{end}fold`)
4. The fold summary is stored in the Lua module's `fold_summaries` table
5. When the fold is closed, Neovim calls the custom `fold_text()` function
6. The function looks up the summary and displays it

## Intended User Experience

### Without Folding (Current)
```
🔧 **Calling tool**: `read_file`
**Arguments**:
  - `path`: `src/main.py`

✅ **Tool result**:
```
[... 50 lines of file content ...]
```
```

### With Folding (Not Working Yet)
```
  🔧 Calling tool: read_file         [folded, press za to open]

  ✅ Tool result                      [folded, press za to open]
```

## Fold Keybindings

Standard Neovim fold commands work:

- `za` - Toggle fold under cursor
- `zo` - Open fold under cursor
- `zc` - Close fold under cursor
- `zR` - Open all folds
- `zM` - Close all folds

## Implementation Notes

1. **Manual Folds**: Uses `foldmethod=manual` to avoid conflicts with other folding methods
2. **0-based Indexing**: Python uses 0-based line numbers, Neovim uses 1-based, conversions are handled carefully
3. **Solution for Async Context Issues**: The problem was resolved by changing the display approach:
   - **Before**: Tool calls were displayed via `append_stream_lua_direct()` with character-by-character animation
   - **After**: Tool calls are now displayed via `append_content()` with instant append
   - This eliminates the async context limitations that prevented fold creation
   - Agent text responses continue to use streaming for visual effect
4. **Error Handling**: Fold creation failures are caught and logged but don't interrupt the flow
5. **Performance**: Instant append for tools is actually faster and more efficient than streaming

## Inspiration

This implementation is inspired by codecompanion.nvim's folding system but simplified for agent.nvim's use case:

- codecompanion tracks different fold types (tool, context, reasoning) with different icons
- agent.nvim currently only folds tool calls and results
- Both use manual folding with custom fold text
- Both store fold summaries in Lua for efficient lookup

## Solution Implemented

**Option #4 was chosen**: Disable streaming for tools, use instant append for tool calls/results, keep streaming only for agent text responses.

This approach was selected because it:
- ✅ Solves the async context issues completely
- ✅ Provides the best user experience (tools appear instantly)
- ✅ Maintains the streaming effect for agent responses (which users enjoy)
- ✅ Is simpler and more maintainable than complex workarounds
- ✅ Improves performance by eliminating unnecessary streaming overhead

## Future Enhancements

Potential improvements for the folding system:

1. Add visual indicators (icons) in the gutter for folded content
2. Support folding of other content types (e.g., long agent responses)
3. Make folding behavior configurable
4. Add highlight groups for fold summaries
5. Fold on demand: Allow users to manually fold tool calls with a keybinding
6. Configurable fold summaries (different verbosity levels)
