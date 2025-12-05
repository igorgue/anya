"""Invisible Unicode markers for embedding metadata in buffer content.

These zero-width characters are preserved by Neovim buffers and allow
reconstructing UI state (folds, extmarks, widgets) from pure text content.
"""

# Fold boundaries
FOLD_START = "\u200b"  # ZWSP - Zero-Width Space
FOLD_END = "\u200c"  # ZWNJ - Zero-Width Non-Joiner

# Tool call status
TOOL_PENDING = "\u200d"  # ZWJ - Zero-Width Joiner (executing, waiting for result)
TOOL_SUCCESS = "\u2060"  # WJ - Word Joiner
TOOL_FAILURE = "\u200e"  # LRM - Left-to-Right Mark

# Edit tool states
EDIT_PENDING = (
    "\u200f"  # RLM - Right-to-Left Mark (patch ready, awaiting user decision)
)
EDIT_APPLIED = "\u034f"  # CGJ - Combining Grapheme Joiner
EDIT_REJECTED = "\u2066"  # LRI - Left-to-Right Isolate
EDIT_FAILED = "\u2067"  # RLI - Right-to-Left Isolate (tool execution failed)
