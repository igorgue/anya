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
  _G.anya_stream_sync_pending = false -- Barrier flag for sync writes
end

if _G.anya_stream_ui_block == nil then
  _G.anya_stream_ui_block = false -- Hard block for any writes while UI prompt is active
end

local AUTOSCROLL_WIN_VAR = "anya_chat_autoscroll"

local function is_chat_window(win)
  if not vim.api.nvim_win_is_valid(win) then
    return false
  end

  local bufnr = vim.api.nvim_win_get_buf(win)
  if not vim.api.nvim_buf_is_valid(bufnr) then
    return false
  end

  return vim.api.nvim_get_option_value("filetype", { buf = bufnr }) == "anya-chat"
end

local function is_window_at_buffer_bottom(win)
  if not is_chat_window(win) then
    return false
  end

  local bufnr = vim.api.nvim_win_get_buf(win)
  local cursor = vim.api.nvim_win_get_cursor(win)
  local line_count = vim.api.nvim_buf_line_count(bufnr)
  return cursor[1] >= line_count
end

function M._set_autoscroll_enabled(win, enabled)
  if not vim.api.nvim_win_is_valid(win) then
    return
  end

  pcall(vim.api.nvim_win_set_var, win, AUTOSCROLL_WIN_VAR, enabled == true)
end

function M._is_autoscroll_enabled(win)
  if not is_chat_window(win) then
    return false
  end

  local ok, enabled = pcall(vim.api.nvim_win_get_var, win, AUTOSCROLL_WIN_VAR)
  if ok then
    return enabled == true
  end

  local at_bottom = is_window_at_buffer_bottom(win)
  M._set_autoscroll_enabled(win, at_bottom)
  return at_bottom
end

function M._refresh_window_autoscroll_state(win)
  if not is_chat_window(win) then
    return
  end

  M._set_autoscroll_enabled(win, is_window_at_buffer_bottom(win))
end

function M._setup_autoscroll_tracking()
  if _G.anya_chat_autoscroll_tracking_setup then
    return
  end
  _G.anya_chat_autoscroll_tracking_setup = true

  local group = vim.api.nvim_create_augroup("AnyaChatAutoscroll", { clear = true })

  vim.api.nvim_create_autocmd({ "BufEnter", "WinEnter", "CursorMoved", "CursorMovedI", "WinScrolled" }, {
    group = group,
    callback = function()
      M._refresh_window_autoscroll_state(vim.api.nvim_get_current_win())
    end,
    desc = "Enable chat autoscroll only while cursor stays on the bottom line",
  })
end

-- Append text chunk to buffer
function M._append_to_buffer(bufnr, chunk)
  local line_count = vim.api.nvim_buf_line_count(bufnr)
  local last_line_idx = line_count - 1
  local last_line = vim.api.nvim_buf_get_lines(bufnr, last_line_idx, last_line_idx + 1, false)
  local last_line_content = last_line[1] or ""

  -- If the chunk starts with a marker and the last line has content,
  -- we need to add a newline first to ensure the marker is on its own line
  local chunk_to_append = chunk
  if chunk:match("^<!%-%- [am]t:") and last_line_content:match("%S") then
    -- Last line has content and chunk starts with a marker - ensure newline separation
    if not last_line_content:match("%s$") then
      -- Last line doesn't end with whitespace, add a newline
      chunk_to_append = "\n" .. chunk
    end
  end

  local last_column = #last_line_content
  local lines = vim.split(chunk_to_append, "\n", { plain = true })
  vim.api.nvim_buf_set_text(bufnr, last_line_idx, last_column, last_line_idx, last_column, lines)
end

-- Autoscroll to bottom of buffer for all windows showing it
function M._autoscroll_to_bottom(bufnr)
  local ft = vim.api.nvim_get_option_value("filetype", { buf = bufnr })

  for _, win in ipairs(vim.api.nvim_list_wins()) do
    if vim.api.nvim_win_get_buf(win) == bufnr and M._is_autoscroll_enabled(win) then
      local new_line_count = vim.api.nvim_buf_line_count(bufnr)
      pcall(vim.api.nvim_win_set_cursor, win, { new_line_count, 0 })
      M._set_autoscroll_enabled(win, true)

      -- Trigger render-markdown to refresh this buffer
      if ft == "anya-chat" then
        vim.api.nvim_exec_autocmds("CursorMoved", { buffer = bufnr })
      end
    end
  end

  -- Always refresh @filepath/#conv_id highlights after render-markdown may have re-rendered,
  -- regardless of whether any window was autoscrolling (cursor may be in the prompt buffer)
  if ft == "anya-chat" and _G.anya_highlight_chat_file_refs then
    _G.anya_highlight_chat_file_refs()
  end
end

-- Force-enable autoscroll on all windows showing this buffer and scroll to bottom.
-- Used when the user sends a message so streaming always auto-follows regardless of cursor position.
function M._force_autoscroll_to_bottom(bufnr)
  for _, win in ipairs(vim.api.nvim_list_wins()) do
    if vim.api.nvim_win_get_buf(win) == bufnr and is_chat_window(win) then
      M._set_autoscroll_enabled(win, true)
    end
  end
  M._autoscroll_to_bottom(bufnr)
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

  -- Ensure marker isolation
  final_text = markers.ensure_marker_line_isolation(final_text)

  table.insert(_G.anya_stream_queue, {
    bufnr = bufnr,
    text = final_text,
  })

  -- If a sync write is pending (barrier active), don't start timer
  -- This preserves ordering when output_sync is deferred via vim.schedule
  if _G.anya_stream_sync_pending then
    return
  end

  -- Start timer if not already running
  M._ensure_timer_running()
end

-- Start the animation timer if not already running
function M._ensure_timer_running()
  if _G.anya_stream_timer then
    return
  end

  -- Don't start timer if a sync write is pending
  if _G.anya_stream_sync_pending then
    return
  end

  local function timer_callback()
    -- Check if paused/UI-blocked - skip processing but keep timer running
    if _G.anya_stream_paused or _G.anya_stream_ui_block then
      return
    end

    -- Check if sync write is pending - stop timer until barrier completes
    if _G.anya_stream_sync_pending then
      if _G.anya_stream_timer then
        _G.anya_stream_timer:stop()
        _G.anya_stream_timer = nil
      end
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
      local ok, err = pcall(M._append_to_buffer, item.bufnr, chunk)
      if not ok then
        if err and (tostring(err):find("E565") or tostring(err):find("textlock")) then
          -- UI is in a textlocked state (e.g. vim.ui.select/inputlist open).
          -- Put the chunk back and retry on the next timer tick.
          item.text = chunk .. item.text
          return
        end
        -- Never hard-error from timer callback; keep queue alive.
        item.text = chunk .. item.text
        return
      end
      M._autoscroll_to_bottom(item.bufnr)
    end

    -- Remove item if all text written
    if item.text == "" then
      -- NOTE: Don't process markers here - let callers do it explicitly
      -- to avoid duplicate processing during tool calls
      table.remove(_G.anya_stream_queue, 1)

      -- If queue is now empty, do a final deferred highlight so the completed
      -- response is always highlighted regardless of cursor position.
      if #_G.anya_stream_queue == 0 and _G.anya_highlight_chat_file_refs then
        _G.anya_highlight_chat_file_refs()
      end
    end
  end

  _G.anya_stream_timer = vim.loop.new_timer()
  -- Start with random delay and keep repeating
  local base_interval = 8
  _G.anya_stream_timer:start(math.random(5, 10), base_interval, vim.schedule_wrap(timer_callback))
end

-- Output text synchronously without streaming animation
-- Text is written immediately to the buffer, bypassing the queue
-- IMPORTANT: This function uses a barrier/snapshot approach to ensure ordering:
-- 1. Set sync_pending flag to prevent new output() calls from starting timer
-- 2. Snapshot the current queue (items queued BEFORE this sync call)
-- 3. Write the snapshot items, then write the sync content
-- 4. Resume timer for items queued AFTER the sync call
-- This ensures fold_end markers are written BEFORE any text that arrives after
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

  -- Ensure marker isolation
  final_text = markers.ensure_marker_line_isolation(final_text)

  -- If UI prompt is active, defer sync write into queue to avoid textlock (E565).
  if _G.anya_stream_paused or _G.anya_stream_ui_block then
    table.insert(_G.anya_stream_queue, { bufnr = bufnr, text = final_text })
    return
  end

  -- CRITICAL: Stop the timer IMMEDIATELY to prevent any race conditions
  if _G.anya_stream_timer then
    _G.anya_stream_timer:stop()
    _G.anya_stream_timer = nil
  end

  -- Set barrier flag BEFORE snapshotting - this prevents output() from
  -- starting the timer for items that arrive while we're processing
  _G.anya_stream_sync_pending = true

  -- Snapshot the current queue (items that arrived BEFORE this sync call)
  -- Replace queue with empty table so new items go to fresh queue
  local pre_queue = _G.anya_stream_queue
  _G.anya_stream_queue = {}

  -- Helper to write all items from a queue snapshot
  local function write_queue_items(items)
    for _, item in ipairs(items) do
      if vim.api.nvim_buf_is_valid(item.bufnr) and item.text ~= "" then
        M._append_to_buffer(item.bufnr, item.text)
      end
    end
  end

  -- Helper to do the actual write
  local function do_write()
    if not vim.api.nvim_buf_is_valid(bufnr) then
      -- Clear barrier and resume timer if needed before returning
      _G.anya_stream_sync_pending = false
      if #_G.anya_stream_queue > 0 then
        M._ensure_timer_running()
      end
      return
    end

    -- Write only items that were queued BEFORE this sync call
    write_queue_items(pre_queue)

    -- Write the sync content itself (e.g., fold_end marker)
    M._append_to_buffer(bufnr, final_text)

    -- Process markers and create folds (unless caller will do it)
    if not skip_process_markers then
      markers_ui._process_markers(bufnr)
    end

    -- Autoscroll to bottom
    M._autoscroll_to_bottom(bufnr)

    -- Clear barrier - new output() calls can now start timer
    _G.anya_stream_sync_pending = false

    -- Resume timer for items queued AFTER this sync call started
    if #_G.anya_stream_queue > 0 then
      M._ensure_timer_running()
    end
  end

  -- Try to write synchronously first (preserves ordering)
  -- Only fall back to vim.schedule if we get E565 textlock error
  local ok, err = pcall(do_write)
  if not ok then
    -- Check if it's a textlock error (E565)
    if err and (err:find("E565") or err:find("textlock")) then
      -- Schedule the write - barrier flag is still set so new output()
      -- calls won't start the timer until do_write completes
      vim.schedule(do_write)
    else
      -- Clear barrier before re-raising error
      _G.anya_stream_sync_pending = false
      if #_G.anya_stream_queue > 0 then
        M._ensure_timer_running()
      end
      error(err)
    end
  end
end

-- Pause the streaming queue (stop writing but keep items queued)
function M.pause_queue()
  _G.anya_stream_paused = true
  _G.anya_stream_ui_block = true
  if _G.anya_stream_timer then
    _G.anya_stream_timer:stop()
    _G.anya_stream_timer = nil
  end
end

-- Resume the streaming queue (continue writing from where it paused)
function M.resume_queue()
  _G.anya_stream_paused = false
  _G.anya_stream_ui_block = false
  -- Restart timer if queue has items
  if #_G.anya_stream_queue > 0 then
    M._ensure_timer_running()
  end
end

-- Clear the streaming queue and stop timer
function M.clear_queue()
  _G.anya_stream_queue = {}
  _G.anya_stream_ui_block = false
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
    paused = _G.anya_stream_paused == true,
    ui_blocked = _G.anya_stream_ui_block == true,
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
        -- Use pcall to handle textlock errors, schedule retry if needed
        local ok, err = pcall(M._append_to_buffer, item.bufnr, item.text)
        if not ok then
          if err and err:match("E565") then
            -- Textlock error - schedule for later
            vim.schedule(function()
              if vim.api.nvim_buf_is_valid(item.bufnr) then
                pcall(M._append_to_buffer, item.bufnr, item.text)
                pcall(M._autoscroll_to_bottom, item.bufnr)
                if process_markers_after then
                  pcall(markers_ui._process_markers, item.bufnr)
                end
              end
            end)
          end
        else
          M._autoscroll_to_bottom(item.bufnr)
        end
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
      pcall(markers_ui._process_markers, bufnr)
    end
  end

  -- After flushing, attempt to drain any queued user messages.
  -- This covers the case where the agent finished but the queue was still
  -- draining when the user submitted their follow-up.
  vim.schedule(function()
    local ok, conv = pcall(require, "anya.conversation")
    if ok and conv._drain_pending_queue then
      conv._drain_pending_queue()
    end
  end)
end

M._setup_autoscroll_tracking()

return M
