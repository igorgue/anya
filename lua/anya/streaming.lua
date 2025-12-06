-- Streaming text animation module for Anya plugin
-- Handles queuing and animated text output to buffers

local M = {}
local markers = require("anya.markers")

-- Initialize global state if not exists
if not _G.anya_stream_queue then
  _G.anya_stream_queue = {}
  _G.anya_stream_timer = nil
end

-- Inject fold markers into text
-- Inserts fold_start marker after first line and fold_end at the end
-- @param text string: Original text
-- @param extra_markers string[]: Additional markers to include with fold_start
-- @return string: Text with marker lines injected
function M._inject_fold_markers(text, extra_markers)
  local lines = vim.split(text, "\n", { plain = true })
  if #lines == 0 then
    return text
  end

  -- Build start marker with fold_start + any extra markers
  local start_names = { markers.fold_start }
  if extra_markers then
    for _, m in ipairs(extra_markers) do
      table.insert(start_names, m)
    end
  end

  -- Insert fold_start after first line, fold_end at the end
  local result = { lines[1], markers.make_marker(unpack(start_names)) }
  for i = 2, #lines do
    table.insert(result, lines[i])
  end
  table.insert(result, markers.make_marker(markers.fold_end))

  return table.concat(result, "\n")
end

-- Queue text for animated output
-- Text may contain marker lines which will be processed after streaming completes
-- If the text starts with a marker line and the previous queued item ends with
-- a blank line, the blank line is replaced to avoid consecutive empty lines.
-- @param bufnr number: Buffer number to write to
-- @param text string: Text to output (can contain newlines and marker lines)
-- @param fold boolean|nil: If true, wrap text with fold markers
function M.output_text(bufnr, text, fold)
  local final_text = text

  -- Inject fold markers if requested
  if fold then
    final_text = M._inject_fold_markers(text)
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
    local chars_to_write = 3
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
      -- Process markers and create folds from buffer content
      M._process_markers(item.bufnr)
      table.remove(_G.anya_stream_queue, 1)
    end
  end

  _G.anya_stream_timer = vim.loop.new_timer()
  -- Start with random delay and keep repeating
  local base_interval = 8
  _G.anya_stream_timer:start(math.random(5, 10), base_interval, vim.schedule_wrap(timer_callback))
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
    end
  end
end

-- Process marker lines in buffer and create folds
-- Scans for fold_start/fold_end marker lines and creates corresponding folds
-- fold_start affects line above it, fold_end is included in the fold
-- @param bufnr number: Buffer number to process
function M._process_markers(bufnr)
  local lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)
  local fold_start_line = nil

  for i, line in ipairs(lines) do
    if markers.is_marker_line(line) then
      local found_markers = markers.parse_marker(line)

      if found_markers then
        for _, marker_name in ipairs(found_markers) do
          if marker_name == markers.fold_start then
            -- fold_start affects line above (i-1 in 1-indexed)
            fold_start_line = i - 1
          elseif marker_name == markers.fold_end and fold_start_line then
            -- fold_end line is included in the fold
            local fold_end_line = i
            if fold_end_line > fold_start_line then
              M._create_fold_range(bufnr, fold_start_line, fold_end_line)
            end
            fold_start_line = nil
          end
        end
      end
    end
  end
end

-- Create a manual fold for a specific range
-- @param bufnr number: Buffer number
-- @param start_line number: Line number where fold should start (1-indexed)
-- @param end_line number: Line number where fold should end (1-indexed)
function M._create_fold_range(bufnr, start_line, end_line)
  -- Find a window displaying this buffer to create the fold
  for _, win in ipairs(vim.api.nvim_list_wins()) do
    if vim.api.nvim_win_get_buf(win) == bufnr then
      -- Save current cursor position
      local cursor = vim.api.nvim_win_get_cursor(win)

      -- Ensure foldmethod is manual for this buffer
      vim.api.nvim_set_option_value("foldmethod", "manual", { win = win })

      -- Create the fold using vim command in the context of the window
      vim.api.nvim_win_call(win, function()
        ---@diagnostic disable-next-line: param-type-mismatch
        pcall(vim.cmd, string.format("%d,%dfold", start_line, end_line))
      end)

      -- Restore cursor position
      pcall(vim.api.nvim_win_set_cursor, win, cursor)

      -- Only need to create fold in one window
      break
    end
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

return M
