# Cancel Compaction Implementation

## Problem
The cancel functionality (`AgentCancel` command / `Ctrl+C`) would only cancel the main agent request but would NOT cancel the `/compact` command summary generation, which runs in a separate thread.

## Solution
Implemented comprehensive cancellation support for the `/compact` command with multiple checkpoints throughout the compaction process.

## Changes Made

### 1. Updated `AgentCancel` Command (plugin.py:373-389)
- Added support for cancelling both agent requests AND compaction operations
- Checks for agent request first, then checks for active compaction
- Uses `_compact_cancelled` flag to signal compaction thread to stop
- Properly handles case when neither is running

```python
# Logic:
# 1. If agent request is active → cancel agent
# 2. If compaction is running (flag is False) → set flag to True to signal stop
# 3. Otherwise → report nothing to cancel
```

### 2. Initialized Cancellation Flag (plugin.py:599-600)
- Added `self._compact_cancelled = False` when `/compact` starts
- Flag is set to `True` when user presses Ctrl+C
- Flag is reset to `False` in cleanup (when compaction thread finishes)

### 3. Added Cancellation Checkpoints (plugin.py:671-757)
Five strategic checkpoints throughout compaction process:

1. **Before analysis** (line 674-680)
   - Catches immediate cancellation requests
   - Exits before any processing begins

2. **After analysis start** (line 687-693)
   - Allows cancellation while analyzing context
   - Cleans up any partial analysis

3. **Before summary generation** (line 705-711)
   - Critical checkpoint before expensive LLM calls
   - Prevents unnecessary API calls

4. **After summary generation** (line 731-737)
   - Allows cancellation after summary is ready but before preview
   - User can still change mind before applying

5. **Before applying compaction** (line 752-758)
   - Final checkpoint after user approves preview
   - Allows cancellation during final application phase

## How It Works

### Starting Compaction
```
User types: /compact aggressively
↓
_compact_cancelled = False (set in _handle_compact_command)
↓
Compaction thread starts in background
```

### Cancelling Compaction
```
User presses: Ctrl+C (AgentCancel mapped)
↓
Check if agent is running → No
↓
Check if _compact_cancelled flag exists and is False → Yes (running)
↓
Set _compact_cancelled = True
↓
Next checkpoint in _perform_compaction sees the flag
↓
Return early with "Compaction cancelled" message
```

### Cleanup After Compaction
```
Compaction thread finishes (naturally or cancelled)
↓
finally block in run_compaction_thread
↓
Set _compact_cancelled = False (cleanup)
↓
Compaction is no longer considered "running"
```

## Benefits

1. **User Control**: Users can now cancel long-running compaction operations
2. **Early Exit**: Multiple checkpoints allow fast exit at different stages
3. **Resource Efficient**: Prevents wasting API calls or processing cycles
4. **Consistent UX**: Cancellation works the same way as for agent requests
5. **No API Waste**: Critical checkpoint before expensive LLM calls

## Technical Details

### Flag States
- `False` = Compaction is running (normal state during execution)
- `True` = Cancel has been requested (thread should stop)
- Not initialized = Compaction is not active

### Why `_compact_cancelled = False` When Running
Using `False` to represent "running" allows the cancel command to detect active compaction:
- `hasattr(self, '_compact_cancelled') and not self._compact_cancelled` → Running
- The flag is only initialized when compaction starts
- It's cleared (set to False) in cleanup, making it safe to check again

### Thread Safety
- Compaction runs in a daemon thread
- Flag check is a simple boolean comparison (thread-safe in Python)
- No locks needed for this simple boolean flag
- Cleanup happens in finally block regardless of how thread exits

## Testing

To test the implementation:

1. Start a compaction: `:AgentOpen` → type `/compact aggressively`
2. While compacting, press `Ctrl+C` to cancel
3. Verify: Should see "Cancelling compaction..." message and "Compaction cancelled" in buffer

Expected outcomes:
- Early cancellation (before analysis): Fast cancel
- Mid-analysis cancellation: Stops after current phase
- Pre-preview cancellation: Stops after summary generation
- Post-approval cancellation: Stops during final application
