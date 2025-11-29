# `/file` Command Feature

## Summary
A new interactive file selection feature has been implemented for agent.nvim that allows users to quickly add multiple files to their prompt using the `/file` slash command.

## Usage

### Basic Usage
```
1. Type: /file
2. Press: Enter
3. Snacks picker appears
4. Select files: Ctrl+Space (toggle selection)
5. Confirm: Enter
6. Result: Files added as @references to prompt
```

### Example Workflow

**Step 1: Open agent and type /file**
```
/file
```

**Step 2: Snacks picker opens**
```
Files in project:
  src/
    main.py
    utils.py
    config.py
  tests/
    test_main.py
    test_utils.py
  README.md
  ...
```

**Step 3: Select files (Ctrl+Space)**
```
Files in project:
  src/
  ★ src/main.py      ← selected
  ★ src/utils.py     ← selected
  tests/
    test_main.py
```

**Step 4: Press Enter to confirm**

**Step 5: Files added to prompt**
```
@src/main.py @src/utils.py
```

**Step 6: Can add message after files**
```
@src/main.py @src/utils.py Can you review this code?
```

## Features

✓ **Multi-select**: Choose any number of files at once  
✓ **Relative paths**: Files use relative paths from project root  
✓ **Syntax highlighted**: `@file` references show with Directory highlighting  
✓ **Preserves prompt**: Existing text is kept and placed after files  
✓ **Works with empty prompts**: Just files if no text entered  
✓ **Integrated with Snacks**: Uses existing Snacks.picker.files() source  
✓ **Error handling**: Shows notifications if picker fails  
✓ **Cursor positioning**: Automatically positions cursor at end of prompt  

## Commands

- `/file` - Open file picker to add multiple files to prompt
- `/help` - Shows updated help including `/file` command

## Implementation

### Core Logic
1. Open Snacks file picker in multi-select mode
2. Get selected items from picker
3. Extract file paths from picker items
4. Prepend files as `@` references to prompt text
5. Update prompt buffer
6. Close picker

### Files Modified
- `rplugin/python3/agent_nvim/plugin.py` - Added command handler
- `AGENTS.md` - Added documentation

### No Breaking Changes
- All existing functionality preserved
- Compatible with existing @ mention syntax
- Works with mention completion
- Works with all other slash commands

## Technical Details

**Language**: Python + Lua  
**Dependencies**: Snacks.nvim (already used by agent.nvim)  
**Buffer operations**: Uses nvim API for buffer manipulation  
**Selection handling**: Compatible with Snacks picker selected() API  

## Quick Test

To test the feature:

```vim
:AgentOpen                    " Open agent interface
```

Then in the prompt buffer:

```
/file
<Enter>
```

Select some files with Ctrl+Space and press Enter. The files should appear in your prompt as `@file` references!

## Error Handling

If something goes wrong:
- **Picker won't open**: Check that Snacks.nvim is installed
- **No prompt buffer**: Shows error notification
- **Selection not applied**: Check buffer validity
- **Wrong directory**: Picker runs from project root

All errors are logged to `~/.local/state/nvim/agent.nvim.log`
