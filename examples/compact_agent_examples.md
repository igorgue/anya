# CompactAgent Usage Examples

## Basic Usage

### Simple Compaction
```
/compact
```
Compact the conversation with automatic settings.

### Aggressive Compaction
```
/compact aggressively
```
Heavily reduce the conversation size (target: ~30% of original).

### Light Compaction
```
/compact lightly
```
Gently reduce the conversation size (target: ~85% of original).

### Target Specific Token Count
```
/compact --tokens=2000
```
Compact to approximately 2000 tokens.

## Natural Language Instructions

### Focus on Specific Topics
```
/compact focus on the authentication flow we are working on
```
Keep only discussions related to authentication.

### Remove Specific Content
```
/compact keep the current API implementation discussion but remove all the debugging and error troubleshooting from earlier
```
Preserve API work, remove debugging sessions.

### Temporal Filtering
```
/compact keep only the discussions from the last hour, remove everything else
```
Focus only on recent conversations.

### Project-Specific Focus
```
/compact preserve everything related to the user authentication module, compress all other discussions
```
Maintain context for specific module.

### Remove Off-Topic Content
```
/compact remove the sidebar about coffee preferences and keep only the programming discussion
```
Filter out non-relevant conversations.

### Multiple Focus Areas
```
/compact preserve discussions about database design, API contracts, and user authentication. Remove the CSS styling and frontend layout conversations.
```
Keep technical discussions, remove UI topics.

## Advanced Usage

### Preserve File References
```
/compact keep all mentions of src/auth.py, src/database.py, and the user model, compress everything else
```
Maintain context for specific files.

### Maintain Action Items
```
/compact ensure all TODO items, next steps, and action items are preserved, compress the rest of the conversation
```
Focus on task-related content.

### Prepare for Handoff
```
/compact summarize this session for someone else to understand what we built, remove the exploratory process and focus on the final implementation
```
Create summary for knowledge transfer.

## Configuration

### Custom Model for Compaction
Set a different model for the compact agent:

```bash
export AGENT_COMPACT_MODEL=gpt-4o-mini
```

### Environment Variables

- `AGENT_COMPACT_MODEL`: Override model for compact agent (default: same as main agent)
- `AGENT_MODEL`: Model used for both main and compact agents
- `OPENAI_API_KEY`: Your OpenAI API key

## Integration Features

### Preview Interface
- Shows side-by-side comparison of original vs compacted content
- Displays token reduction statistics
- Allows editing before accepting
- Keybindings:
  - `<Enter>`/`y`: Accept compaction
  - `<Esc>`/`n`: Cancel
  - `e`: Edit summary
  - `r`: Regenerate (future feature)

### Context Preservation
The CompactAgent automatically preserves:
- Active tasks and ongoing work
- Key decisions and conclusions
- Important file references
- Action items and next steps
- Technical details and code snippets

### Smart Token Targeting
When using natural language instructions, the system automatically infers appropriate token targets:

- "aggressive/heavily/drastically" → ~30% reduction
- "significantly/moderately" → ~50% reduction  
- "lightly/minimally/gently" → ~85% reduction
- Specific numbers ("around 2000 tokens") → exact target

## Error Handling

### Common Issues

1. **"Compact agent not available"**
   - Check OpenAI agents SDK installation
   - Verify API key is set
   - Run `:AgentInstall` to update dependencies

2. **"No conversation to compact"**
   - Need at least some conversation history
   - Try having a conversation first

3. **Preview modal doesn't appear**
   - Check if Snacks.nvim is installed
   - Falls back to native Neovim windows automatically

### Troubleshooting

```vim
:AgentTestImport  " Test if dependencies are available
:AgentInstall    " Install/update dependencies
```

Check logs for detailed error messages:
```bash
tail -f ~/.local/state/nvim/agent.nvim.log
```