# Quick Start: `/file` Command

## 30-Second Overview

The `/file` command opens an interactive file picker where you can select multiple files to include in your agent prompt. Selected files are automatically added as `@filename` references.

## Installation

No installation needed! The feature is built-in and enabled by default.

**Requirements:**
- agent.nvim installed and working
- Snacks.nvim available (should already be installed if you have agent.nvim)

## Usage

### 1. Open Agent
```vim
:AgentOpen
```

### 2. Type the Command
In the prompt buffer, type:
```
/file
```

### 3. Press Enter
The Snacks file picker opens showing all project files.

### 4. Select Files
Use these keys to navigate and select:
- `↑/↓` - Navigate up and down
- `Ctrl+Space` - Toggle file selection (adds ☑ or ☐)
- `Enter` - Confirm selection and close picker

### 5. Files Appear in Prompt
Your selected files now appear as references:
```
@src/main.py @tests/test.py
```

### 6. Optional: Add a Message
Type your message after the files:
```
@src/main.py @tests/test.py Can you review this code?
```

### 7. Submit
Press Enter to send to the agent.

## Common Workflows

### Review Multiple Files
```
/file
→ Select: src/main.py, src/utils.py, tests/test.py
→ Type: "Can you review these files for bugs?"
→ Enter
```

### Explain a Feature
```
/file
→ Select: src/feature.py, docs/feature.md
→ Type: "Explain how this feature works"
→ Enter
```

### Debug Issue
```
/file
→ Select: src/error.py, logs/debug.log, config.py
→ Type: "Why is this failing?"
→ Enter
```

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `↑/↓` | Navigate files |
| `Enter` | Select/deselect or confirm |
| `Ctrl+Space` | Toggle selection |
| `Esc` | Cancel picker |
| `>` | Expand directory |
| `<` | Collapse directory |
| `j/k` | Down/up (vim keys) |

## Tips & Tricks

### Tip 1: Multiple Files
Select as many files as you need. They all appear on one line:
```
@file1 @file2 @file3 @file4 Your message here
```

### Tip 2: Folder Organization
Files are grouped in the picker, making it easy to find related files:
```
src/
  main.py (select)
  utils.py (select)
tests/
  test_main.py (select)
```

### Tip 3: Cancel Anytime
Press `Esc` to cancel without making changes. Your previous prompt is preserved.

### Tip 4: Search in Picker
Type to search for files by name:
```
Picker is open, type "test" → shows only test*.py files
```

### Tip 5: Clear and Start Over
If you made a mistake, use `/clear` to reset, then `/file` again:
```
/clear
→ (chat history cleared)
/file
→ (start fresh file selection)
```

## Troubleshooting

### Q: Command doesn't open picker
**A:** Make sure Snacks.nvim is installed and working. Check:
```vim
:lua print(Snacks ~= nil)  " Should print true
```

### Q: No files appear in picker
**A:** Make sure you're in a project directory with files. Try:
```vim
:pwd  " Check current working directory
```

### Q: Selected files aren't showing in prompt
**A:** Check that the prompt buffer is focused. Try clicking on it first.

### Q: Getting "filetype" error
**A:** This is a Neovim version issue. Update Neovim or check logs:
```vim
:tail ~/.local/state/nvim/agent.nvim.log
```

## Help & Documentation

Get help in multiple ways:

**In-app help:**
```
/help
```

**Read documentation:**
- `AGENTS.md` - Full technical documentation
- `FILE_COMMAND_FEATURE.md` - Feature overview
- `FLOW_DIAGRAM.md` - How it works internally
- `FEATURE_SUMMARY.md` - Complete summary

**Check logs:**
```bash
tail -f ~/.local/state/nvim/agent.nvim.log
```

## Examples

### Example 1: Review Python Code
```
Prompt: /file
Select: main.py, utils.py, helpers.py
Type: Review for style and bugs
Result sent to agent:
  @src/main.py @src/utils.py @src/helpers.py Review for style and bugs
```

### Example 2: Understand Configuration
```
Prompt: /file
Select: config.json, env.example, .github/workflows/deploy.yml
Type: Explain the configuration setup
Result sent to agent:
  @config.json @env.example @.github/workflows/deploy.yml Explain the configuration setup
```

### Example 3: Fix Error
```
Prompt: /file
Select: error_log.txt, source_file.py, test_file.py
Type: Why am I getting this error?
Result sent to agent:
  @error_log.txt @source_file.py @test_file.py Why am I getting this error?
```

## What's Next?

After using `/file`:
- Agent receives your message with full file content
- Agent analyzes the files
- Agent provides response/solutions
- You can follow up with more questions
- Files stay in context for further discussion

## Need Help?

- **Problem with command?** Check `/help` output
- **Feature not working?** See FLOW_DIAGRAM.md for how it works
- **Want more features?** See FEATURE_SUMMARY.md for future ideas
- **Technical details?** Read IMPLEMENTATION_NOTES.md

---

**Happy file selecting!** 🎉
