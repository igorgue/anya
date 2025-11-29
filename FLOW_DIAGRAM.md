# `/file` Command Flow Diagram

## Complete Execution Flow

```
┌─────────────────────────────────────────────────────────────────┐
│ User in Prompt Buffer                                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  > /file                                                        │
│                                                                 │
│  [highlighted in Special color]                                 │
└─────────────────────────────────────────────────────────────────┘
              │
              │ Press Enter
              ↓
┌─────────────────────────────────────────────────────────────────┐
│ AgentSubmit Command                                             │
├─────────────────────────────────────────────────────────────────┤
│ • Gets prompt text: "/file"                                     │
│ • Clears prompt buffer                                          │
│ • Detects slash command                                         │
└─────────────────────────────────────────────────────────────────┘
              │
              ↓
┌─────────────────────────────────────────────────────────────────┐
│ _handle_slash_command()                                         │
├─────────────────────────────────────────────────────────────────┤
│ • Parses command: "/file"                                       │
│ • Routes to: _handle_file_command()                             │
└─────────────────────────────────────────────────────────────────┘
              │
              ↓
┌─────────────────────────────────────────────────────────────────┐
│ _handle_file_command()                                          │
├─────────────────────────────────────────────────────────────────┤
│ • Executes Lua code                                             │
│ • Opens Snacks file picker                                      │
└─────────────────────────────────────────────────────────────────┘
              │
              ↓
┌─────────────────────────────────────────────────────────────────┐
│ Snacks.picker.files()                                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Files                   [multi-select enabled]                 │
│  ┌──────────────────────────────────────────┐                  │
│  │ □ .gitignore                             │                  │
│  │ □ AGENTS.md                              │                  │
│  │ ☑ src/main.py          ← selected        │                  │
│  │ □ src/utils.py                           │                  │
│  │ ☑ tests/test.py        ← selected        │                  │
│  │ □ README.md                              │                  │
│  └──────────────────────────────────────────┘                  │
│                                                                 │
│  [Ctrl+Space to toggle, Enter to confirm]                      │
└─────────────────────────────────────────────────────────────────┘
              │
              │ User: Selects files (Ctrl+Space)
              │ User: Presses Enter to confirm
              ↓
┌─────────────────────────────────────────────────────────────────┐
│ confirm Action (Custom)                                         │
├─────────────────────────────────────────────────────────────────┤
│ • Gets selected items: picker:selected()                        │
│ • Builds files list:                                            │
│   - src/main.py                                                 │
│   - tests/test.py                                               │
│ • Calls: apply_files_to_prompt(files)                           │
│ • Calls: picker:close()                                         │
└─────────────────────────────────────────────────────────────────┘
              │
              ↓
┌─────────────────────────────────────────────────────────────────┐
│ apply_files_to_prompt()                                         │
├─────────────────────────────────────────────────────────────────┤
│ • Finds agent-prompt buffer by filetype                         │
│ • Gets current prompt text (empty in this case)                │
│ • Builds @ references:                                          │
│   @src/main.py @tests/test.py                                   │
│ • Updates prompt buffer with vim.api.nvim_buf_set_lines()      │
│ • Sets cursor to end of text                                    │
└─────────────────────────────────────────────────────────────────┘
              │
              ↓
┌─────────────────────────────────────────────────────────────────┐
│ Prompt Buffer Updated                                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  > @src/main.py @tests/test.py                                  │
│                          │                                      │
│                   Cursor positioned here                         │
│                                                                 │
│  [@ references highlighted as Directory color]                 │
└─────────────────────────────────────────────────────────────────┘
              │
              │ User can now add message and submit
              ↓
┌─────────────────────────────────────────────────────────────────┐
│ User Types Message (Optional)                                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  > @src/main.py @tests/test.py Can you review this code?        │
│                                                                 │
│  [Files ready to be sent with message to agent]                │
│  [Message will be resolved and agent will read files]          │
└─────────────────────────────────────────────────────────────────┘
```

## Key Components

### Python Layer
- `plugin.py::_handle_slash_command()` - Routes `/file` commands
- `plugin.py::_handle_file_command()` - Opens picker via Lua

### Lua Layer
- `apply_files_to_prompt()` - Applies selected files to prompt
- `confirm` action - Handles file selection confirmation

### Snacks Integration
- Uses existing `Snacks.picker.files()` source
- Multi-select mode enabled
- Custom `confirm` action for file application

## Data Flow

```
/file (text)
    ↓
AgentSubmit
    ↓
_handle_slash_command(cmd="/file")
    ↓
_handle_file_command()
    ↓
Lua: Snacks.picker.files({multi=true, actions={confirm=...}})
    ↓
User selects files
    ↓
confirm action: picker:selected() → extract paths
    ↓
apply_files_to_prompt(files)
    ↓
@file1 @file2 @file3 [added to prompt]
    ↓
vim.api.nvim_buf_set_lines() [update buffer]
    ↓
Prompt buffer ready for message input
```

## Error Handling

```
Try to open picker
    │
    ├─→ Snacks not available? → Error notification
    │
    ├─→ Buffer not found? → Validation fails
    │       ↓
    │   vim.notify(error)
    │
    └─→ Success → Files added to prompt
```

## User Interaction Timeline

1. **User Action**: Type `/file` in prompt buffer
2. **User Action**: Press Enter to submit
3. **Plugin**: Detects slash command
4. **Plugin**: Opens file picker
5. **User Action**: Browse files with arrow keys
6. **User Action**: Toggle selection with Ctrl+Space
7. **User Action**: Press Enter to confirm
8. **Plugin**: Applies selected files to prompt
9. **User**: Now sees `@file @file` in prompt
10. **User**: Can type message and submit normally

## Status Summary

- **Implementation**: ✓ Complete
- **Syntax Highlighting**: ✓ Supported
- **Error Handling**: ✓ Implemented
- **Documentation**: ✓ Comprehensive
- **Testing**: ✓ Manual testing recommended
