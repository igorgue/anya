# Testing /file Command

## Implementation Summary

Added a new slash command `/file` that opens Snacks file picker to select multiple files and adds them as `@` references to the prompt.

### How it works:

1. User types `/file` in the agent prompt buffer
2. Hitting Enter triggers `AgentSubmit` which calls `_handle_slash_command()`
3. For `/file` command, it calls `_handle_file_command()`
4. This opens `Snacks.picker.files()` with `multi = true` to allow selecting multiple files
5. When user confirms selection (Enter key), the `confirm` action:
   - Gets all selected items from the picker
   - Extracts file paths from `item.file` or `item.text`
   - Prepends all selected files as `@filename` references to the current prompt text
   - Updates the prompt buffer with the new text
   - Closes the picker
6. The files are now in the prompt with proper `@` syntax highlighting

### Features:

- **Multi-select**: Select one or more files at once
- **Relative paths**: Files are added with their relative paths
- **Prepends to prompt**: Files are added at the beginning of existing prompt text
- **Works with empty prompts**: If no text in prompt, only files are added
- **Syntax highlighting**: `/file` command and `@file` references are highlighted

### Usage Example:

```
/file
```

Then select files from the picker. Example result:
```
@src/main.py @tests/test_main.py Here's my implementation
```

## Testing Steps

1. Open agent interface: `:AgentOpen`
2. In the prompt buffer, type `/file` and press Enter
3. Snacks file picker should open at the root directory
4. Select multiple files using Ctrl+Space (or configured multi-select binding)
5. Press Enter to confirm
6. Files should be added to the prompt as `@file1.py @file2.py` etc.
7. Can also add a message after the files
8. Hit Enter again to submit to the agent
