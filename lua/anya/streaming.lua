-- Streaming text animation module for Anya plugin
-- Handles queuing and animated text output to buffers

local M = {}
local markers = require("anya.markers")
local markers_ui = require("anya.markers_ui")

-- Initialize global state if not exists
if not _G.anya_stream_queue then
  _G.anya_stream_queue = {}
  _G.anya_stream_timer = nil
  _G.anya_stream_paused = false -- Pause flag for animation queue
end

-- Append text chunk to buffer
function M._append_to_buffer(bufnr, chunk)
  local line_count = vim.api.nvim_buf_line_count(bufnr)
  local last_line_idx = line_count - 1
  local last_line = vim.api.nvim_buf_get_lines(bufnr, last_line_idx, last_line_idx + 1, false)
  local last_column = #(last_line[1] or "")

  local lines = vim.split(chunk, "\n", { plain = true })
  vim.api.nvim_buf_set_text(bufnr, last_line_idx, last_column, last_line_idx, last_column, lines)
end

-- Autoscroll to bottom of buffer for all windows showing it
function M._autoscroll_to_bottom(bufnr)
  for _, win in ipairs(vim.api.nvim_list_wins()) do
    if vim.api.nvim_win_get_buf(win) == bufnr then
      local new_line_count = vim.api.nvim_buf_line_count(bufnr)
      pcall(vim.api.nvim_win_set_cursor, win, { new_line_count, 0 })

      -- Trigger render-markdown to refresh this buffer
      local ft = vim.api.nvim_get_option_value("filetype", { buf = bufnr })
      if ft == "anya-chat" then
        vim.api.nvim_exec_autocmds("CursorMoved", { buffer = bufnr })
        -- Refresh @filepath highlights
        if _G.anya_highlight_chat_file_refs then
          _G.anya_highlight_chat_file_refs()
        end
      end
    end
  end
end

-- Queue text for animated output
-- Text may contain marker lines which will be processed after streaming completes
-- If the text starts with a marker line and the previous queued item ends with
-- a blank line, the blank line is replaced to avoid consecutive empty lines.
-- @param bufnr number: Buffer number to write to
-- @param text string: Text to output (can contain newlines and marker lines)
-- @param marker_list string[]|nil: List of markers to inject (e.g., {"fold", "tool_success"})
function M.output(bufnr, text, marker_list)
  local final_text = text

  -- Inject markers if requested (check type because Python None becomes userdata)
  if type(marker_list) == "table" and #marker_list > 0 then
    final_text = markers_ui._inject_markers(text, marker_list)
  end

  -- Check if text starts with a marker line
  local first_line = final_text:match("^([^\n]*)")
  local starts_with_marker = first_line and markers.is_marker_line(first_line)

  -- If starting with marker, check if previous queue item ends with blank line
  if starts_with_marker and #_G.anya_stream_queue > 0 then
    local prev_item = _G.anya_stream_queue[#_G.anya_stream_queue]
    if prev_item.bufnr == bufnr and prev_item.text:match("\n$") then
      -- Remove trailing newline from previous item
      prev_item.text = prev_item.text:gsub("\n$", "")
    end
  end

  table.insert(_G.anya_stream_queue, {
    bufnr = bufnr,
    text = final_text,
  })

  -- Start timer if not already running
  M._ensure_timer_running()
end

-- Start the animation timer if not already running
function M._ensure_timer_running()
  if _G.anya_stream_timer then
    return
  end

  local function timer_callback()
    -- Check if paused - skip processing but keep timer running
    if _G.anya_stream_paused then
      return
    end

    -- Check if queue is empty
    if #_G.anya_stream_queue == 0 then
      if _G.anya_stream_timer then
        _G.anya_stream_timer:stop()
        _G.anya_stream_timer = nil
      end
      return
    end

    local item = _G.anya_stream_queue[1]

    -- Validate buffer still exists
    if not vim.api.nvim_buf_is_valid(item.bufnr) then
      table.remove(_G.anya_stream_queue, 1)
      return
    end

    -- Vary characters written for natural effect
    local rand = math.random()
    local chars_to_write
    if rand < 0.1 then
      chars_to_write = 1 -- 10% very slow
    elseif rand < 0.25 then
      chars_to_write = 2 -- 15% slow
    elseif rand < 0.6 then
      chars_to_write = 3 -- 35% normal
    elseif rand < 0.8 then
      chars_to_write = 4 -- 20% fast
    else
      chars_to_write = 5 -- 20% very fast
    end

    local chunk = item.text:sub(1, chars_to_write)
    item.text = item.text:sub(chars_to_write + 1)

    if chunk ~= "" then
      M._append_to_buffer(item.bufnr, chunk)
      M._autoscroll_to_bottom(item.bufnr)
    end

    -- Remove item if all text written
    if item.text == "" then
      -- NOTE: Don't process markers here - let callers do it explicitly
      -- to avoid duplicate processing during tool calls
      table.remove(_G.anya_stream_queue, 1)
    end
  end

  _G.anya_stream_timer = vim.loop.new_timer()
  -- Start with random delay and keep repeating
  local base_interval = 8
  _G.anya_stream_timer:start(math.random(5, 10), base_interval, vim.schedule_wrap(timer_callback))
end

-- Output text synchronously without streaming animation
-- Text is written immediately to the buffer, bypassing the queue
-- @param bufnr number: Buffer number to write to
-- @param text string: Text to output (can contain newlines and marker lines)
-- @param marker_list string[]|nil: List of markers to inject (e.g., {"fold", "tool_success"})
-- @param skip_process_markers boolean|nil: If true, skip processing markers (caller will do it)
function M.output_sync(bufnr, text, marker_list, skip_process_markers)
  -- Validate buffer
  if not vim.api.nvim_buf_is_valid(bufnr) then
    return
  end

  local final_text = text

  -- Inject markers if requested (check type because Python None becomes userdata)
  if type(marker_list) == "table" and #marker_list > 0 then
    final_text = markers_ui._inject_markers(text, marker_list)
  end

  -- Write all text at once
  M._append_to_buffer(bufnr, final_text)

  -- Process markers and create folds (unless caller will do it)
  if not skip_process_markers then
    markers_ui._process_markers(bufnr)
  end

  -- Autoscroll to bottom
  M._autoscroll_to_bottom(bufnr)
end

-- Pause the streaming queue (stop writing but keep items queued)
function M.pause_queue()
  _G.anya_stream_paused = true
  if _G.anya_stream_timer then
    _G.anya_stream_timer:stop()
    _G.anya_stream_timer = nil
  end
end

-- Resume the streaming queue (continue writing from where it paused)
function M.resume_queue()
  _G.anya_stream_paused = false
  -- Restart timer if queue has items
  if #_G.anya_stream_queue > 0 then
    M._ensure_timer_running()
  end
end

-- Clear the streaming queue and stop timer
function M.clear_queue()
  _G.anya_stream_queue = {}
  if _G.anya_stream_timer then
    _G.anya_stream_timer:stop()
    _G.anya_stream_timer = nil
  end
end

-- Get current queue status
function M.get_queue_status()
  return {
    queue_length = #_G.anya_stream_queue,
    timer_running = _G.anya_stream_timer ~= nil,
  }
end

-- Flush the streaming queue by writing all remaining text immediately
-- Used when cancelling to finish streaming animation without waiting
-- @param process_markers_after boolean|nil: If true, process markers after flushing (default: true)
function M.flush_queue(process_markers_after)
  -- Default to processing markers after flush
  if process_markers_after == nil then
    process_markers_after = true
  end

  -- Track which buffers we flushed to
  local flushed_buffers = {}

  -- Process all remaining items in the queue
  while #_G.anya_stream_queue > 0 do
    local item = _G.anya_stream_queue[1]

    -- Validate buffer still exists
    if vim.api.nvim_buf_is_valid(item.bufnr) then
      -- Write all remaining text at once
      if item.text ~= "" then
        M._append_to_buffer(item.bufnr, item.text)
        M._autoscroll_to_bottom(item.bufnr)
      end

      flushed_buffers[item.bufnr] = true
    end

    table.remove(_G.anya_stream_queue, 1)
  end

  -- Stop the timer if running
  if _G.anya_stream_timer then
    _G.anya_stream_timer:stop()
    _G.anya_stream_timer = nil
  end

  -- Process markers once per buffer if requested
  if process_markers_after then
    for bufnr, _ in pairs(flushed_buffers) do
      markers_ui._process_markers(bufnr)
    end
  end
end

return M
