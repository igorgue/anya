# `/file` Command Implementation Notes

## Overview
A new slash command `/file` has been implemented that opens Snacks.nvim's file picker in multi-select mode and adds selected files as `@filename` references to the prompt buffer.

## Implementation Details

### Changes Made

1. **Python Plugin (`rplugin/python3/agent_nvim/plugin.py`)**
   - Added `/file` case in `_handle_slash_command()` method
   - Created new `_handle_file_command()` method that:
     - Executes Lua code to open Snacks file picker
     - Handles file selection and prompt buffer updates
     - Provides error handling with user-facing notifications

2. **Documentation (`AGENTS.md`)**
   - Added `/file` to slash commands list
   - Documented detailed usage steps
   - Provided examples of typical usage patterns

3. **Syntax Highlighting**
   - Already supported by existing regex pattern in `syntax/agent-prompt.vim`
   - Pattern `/[a-z]+` matches `/file` command
   - Files are highlighted with `Directory` coloring when prefixed with `@`

### How It Works

1. User types `/file` in the agent-prompt buffer
2. Pressing Enter triggers `AgentSubmit` which parses the slash command
3. `_handle_slash_command()` routes to `/file` handler
4. Lua code opens `Snacks.picker.files()` with:
   - `multi = true` to allow multiple selections
   - Custom `confirm` action that:
     - Gets selected items from the picker
     - Extracts file paths from `item.file` or `item.text`
     - Prepends files as `@` references to current prompt
     - Updates buffer content
     - Positions cursor at end
     - Closes the picker

### Edge Cases Handled

- **Empty prompt**: If no text in prompt, only files are added
- **Prompt with existing text**: Files prepended, text follows on same line
- **Multiline text**: Properly joins with newlines and strips extra whitespace
- **No files selected**: Returns silently without modifying prompt
- **Buffer not found**: Shows error notification if prompt buffer is invalid
- **Invalid buffer**: Validates buffer with `vim.api.nvim_buf_is_valid()`

### Lua Implementation Details

```lua
local function apply_files_to_prompt(files)
    -- 1. Find agent-prompt buffer by filetype check
    -- 2. Get current prompt text (stripping leading/trailing newlines)
    -- 3. Build @ references for each file
    -- 4. Concatenate files + existing text
    -- 5. Update buffer with vim.api.nvim_buf_set_lines()
    -- 6. Set cursor to end of text
end
```

The custom confirm action:
```lua
confirm = function(picker)
    local items = picker:selected({fallback = false})
    local files = {}
    for _, item in ipairs(items) do
        table.insert(files, item.file or item.text)
    end
    apply_files_to_prompt(files)
    picker:close()
end
```

### File Path Extraction

The implementation tries two sources for file path:
1. `item.file` - Primary source from Snacks file picker
2. `item.text` - Fallback if `file` field is not available

This ensures compatibility with different Snacks picker sources if needed in future.

### User Experience

- **Visual feedback**: Snacks picker shows files in a nice UI
- **Multi-select**: Ctrl+Space (or configured keybinding) to toggle selection
- **Syntax highlighting**: Selected `@file` references are highlighted as Directory
- **Workflow**: Natural integration with existing mention syntax

### Testing Recommendations

1. Test with empty prompt
2. Test with existing prompt text
3. Select single file
4. Select multiple files
5. Cancel picker (should not modify prompt)
6. Files with spaces in names
7. Files in subdirectories
8. Verify @ references are properly syntax highlighted
9. Verify file paths are relative to project root
10. Add text after files: `@file1 @file2 Here's my question`

### Future Enhancements

- Add `/file-absolute` for absolute paths
- Add filtering by file extension in picker
- Add confirmation message showing selected files
- Add undo history for last file selection
- Support for other picker sources (recent files, git files, etc.)
