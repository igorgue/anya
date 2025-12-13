-- Streaming text animation module for Anya plugin
-- Handles queuing and animated text output to buffers
-- Refactored into smaller modules: streaming, markers_ui, ui_utils

local M = {}
local ui_utils = require("anya.ui_utils")
local markers_ui = require("anya.markers_ui")
local streaming = require("anya.streaming")

-- Initialize highlight groups
ui_utils.setup_highlights()

-- Export Streaming functions
M.output = streaming.output
M.output_sync = streaming.output_sync
M.pause_queue = streaming.pause_queue
M.resume_queue = streaming.resume_queue
M.clear_queue = streaming.clear_queue
M.flush_queue = streaming.flush_queue
M.get_queue_status = streaming.get_queue_status

-- Internal streaming functions (exposed for compatibility)
M._ensure_timer_running = streaming._ensure_timer_running
M._append_to_buffer = streaming._append_to_buffer
M._autoscroll_to_bottom = streaming._autoscroll_to_bottom

-- Export Markers UI functions
M.update_edit_state = markers_ui.update_edit_state
M.update_edit_state_at_cursor = markers_ui.update_edit_state_at_cursor
M.get_edit_extmark_at_line = markers_ui.get_edit_extmark_at_line

-- Internal markers UI functions (exposed for compatibility)
M._inject_markers = markers_ui._inject_markers
M._process_markers = markers_ui._process_markers
M._create_fold_range = markers_ui._create_fold_range
M._hide_line = markers_ui._hide_line
M._hide_line_with_duration = markers_ui._hide_line_with_duration
M._clear_folds = markers_ui._clear_folds
M._apply_header_highlight = markers_ui._apply_header_highlight
M._apply_message_info = markers_ui._apply_message_info
M._calculate_duration = markers_ui._calculate_duration
M._apply_edit_header = markers_ui._apply_edit_header

return M
