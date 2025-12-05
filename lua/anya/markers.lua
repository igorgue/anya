-- Invisible Unicode markers for embedding metadata in buffer content.
-- These zero-width characters are preserved by Neovim buffers and allow
-- reconstructing UI state (folds, extmarks, widgets) from pure text content.

local M = {}

-- Fold boundaries
M.fold_start = "\u{200b}"    -- ZWSP - Zero-Width Space
M.fold_end = "\u{200c}"      -- ZWNJ - Zero-Width Non-Joiner

-- Tool call status
M.tool_pending = "\u{200d}"  -- ZWJ - Zero-Width Joiner (executing, waiting for result)
M.tool_success = "\u{2060}"  -- WJ - Word Joiner
M.tool_failure = "\u{200e}"  -- LRM - Left-to-Right Mark

-- Edit tool states
M.edit_pending = "\u{200f}"  -- RLM - Right-to-Left Mark (patch ready, awaiting user decision)
M.edit_applied = "\u{034f}"  -- CGJ - Combining Grapheme Joiner
M.edit_rejected = "\u{2066}" -- LRI - Left-to-Right Isolate
M.edit_failed = "\u{2067}"   -- RLI - Right-to-Left Isolate (tool execution failed)

return M
