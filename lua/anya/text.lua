-- Streaming text animation module for Anya plugin
-- Handles queuing and animated text output to buffers

local M = {}
local markers = require("anya.markers")

local icons = {
  pending = "", -- Circle for pending
  success = "", -- Checkmark for success
  failure = "", -- Cross for failure
  thinking = "󰧑", -- Thinking brain for thinking reasoning text
}

-- Namespace for extmarks
local ns_id = vim.api.nvim_create_namespace("anya_markers")

-- Initialize global state if not exists
if not _G.anya_stream_queue then
  _G.anya_stream_queue = {}
  _G.anya_stream_timer = nil
end

-- Track edit extmarks for state updates: { [extmark_id] = { bufnr, line_num, state, diff_info } }
if not _G.anya_edit_extmarks then
  _G.anya_edit_extmarks = {}
end

-- Helper to create highlight with fg from source but transparent bg for combining
local function set_hl_fg_only(name, source, extra)
  local hl = vim.api.nvim_get_hl(0, { name = source, link = false })
  local opts = {
    fg = hl.fg,
    bg = "NONE",
    sp = hl.sp,
    blend = 0, -- Fully opaque fg, transparent bg for hl_mode="combine"
  }
  if extra then
    for k, v in pairs(extra) do
      opts[k] = v
    end
  end
  vim.api.nvim_set_hl(0, name, opts)
end

-- Setup highlight groups (fg inherited, bg transparent)
local function setup_highlights()
  -- Success: green (from OkMsg)
  set_hl_fg_only("AnyaToolSuccess", "OkMsg")

  -- Failure: red (from ErrorMsg)
  set_hl_fg_only("AnyaToolFailure", "ErrorMsg")

  -- Pending: subtle (from Comment)
  set_hl_fg_only("AnyaToolPending", "Comment")

  -- Thinking: gray for reasoning text (from Comment)
  set_hl_fg_only("AnyaThinking", "Comment")

  -- Edit tool highlight groups
  -- Diff indicators
  set_hl_fg_only("AnyaEditAdd", "OkMsg")
  set_hl_fg_only("AnyaEditChange", "WarningMsg")
  set_hl_fg_only("AnyaEditDelete", "ErrorMsg")

  -- Filename (from Constant)
  set_hl_fg_only("AnyaEditFilename", "Constant")

  -- Widget text (from Normal)
  set_hl_fg_only("AnyaEditWidget", "Normal")

  -- Widget text bold variant (for selected action)
  set_hl_fg_only("AnyaEditWidgetBold", "Normal", { bold = true })
end

-- Ensure highlights are set up
setup_highlights()

-- Inject markers into text
-- If markers include "fold", inserts fold_start after first line and fold_end at end
-- All markers are combined into a single marker line after the first line
-- @param text string: Original text
-- @param marker_list string[]: List of marker names (e.g., {"fold", "tool_success"})
-- @return string: Text with marker lines injected
function M._inject_markers(text, marker_list)
  local lines = vim.split(text, "\n", { plain = true })
  if #lines == 0 or not marker_list or #marker_list == 0 then
    return text
  end

  -- Check if fold is requested
  local has_fold = false
  local start_markers = {}

  for _, m in ipairs(marker_list) do
    if m == "fold" then
      has_fold = true
      table.insert(start_markers, markers.fold_start)
    else
      table.insert(start_markers, m)
    end
  end

  -- Build result with marker line after first line
  local result = { lines[1], markers.make_marker(unpack(start_markers)) }
  for i = 2, #lines do
    table.insert(result, lines[i])
  end

  -- Add fold_end if fold was requested
  if has_fold then
    table.insert(result, markers.make_marker(markers.fold_end))
  end

  return table.concat(result, "\n")
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

  -- Inject markers if requested
  if marker_list and #marker_list > 0 then
    final_text = M._inject_markers(text, marker_list)
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

      -- Trigger render-markdown to refresh this buffer
      local ft = vim.api.nvim_get_option_value("filetype", { buf = bufnr })
      if ft == "anya-chat" then
        vim.api.nvim_exec_autocmds("CursorMoved", { buffer = bufnr })
      end
    end
  end
end

-- Parse edit header line to extract diff info
-- Format: "27+ 2~ 30- | README.md"
-- @param line string: The header line content
-- @return table: { added = number, changed = number, deleted = number, filename = string }
local function parse_edit_header(line)
  local diff_info = {}

  -- Parse diff indicators: "27+" "2~" "30-"
  for num, indicator in line:gmatch("(%d+)([+~-])") do
    local n = tonumber(num) or 0
    if indicator == "+" then
      diff_info.added = n
    elseif indicator == "~" then
      diff_info.changed = n
    elseif indicator == "-" then
      diff_info.deleted = n
    end
  end

  -- Parse filename after "|"
  local filename = line:match("|%s*(.+)%s*$")
  if filename then
    diff_info.filename = vim.trim(filename)
  end

  return diff_info
end

-- Apply message info extmark (right-aligned virtual text)
-- For user messages: displays local time (e.g., "2:30pm")
-- For agent messages: displays "agent_type | model" (e.g., "code | gpt-4.1")
-- @param bufnr number: Buffer number
-- @param line_num number: Line number to apply extmark to (1-indexed, the line above the marker)
-- @param msg_info table: Parsed message info from markers.parse_message_marker
function M._apply_message_info(bufnr, line_num, msg_info)
  if line_num < 1 then
    return
  end

  -- Convert to 0-indexed for API
  local line_idx = line_num - 1

  -- Build the display text
  local display_text
  if msg_info.is_agent then
    -- Agent message: "code | gpt-4.1"
    display_text = msg_info.agent_type .. " | " .. msg_info.model
  else
    -- User message: convert UTC timestamp to local time
    display_text = markers.utc_to_local_time(msg_info.timestamp)
  end

  -- Create right-aligned virtual text
  vim.api.nvim_buf_set_extmark(bufnr, ns_id, line_idx, 0, {
    virt_text = { { display_text .. " ", "AnyaToolSuccess" } },
    virt_text_pos = "right_align",
    hl_mode = "combine",
  })
end

-- Hide a marker line by replacing it with empty virtual text
-- @param bufnr number: Buffer number
-- @param line_num number: Line number to hide (1-indexed)
function M._hide_line(bufnr, line_num)
  if line_num < 1 then
    return
  end
  local line_idx = line_num - 1
  local lines = vim.api.nvim_buf_get_lines(bufnr, line_idx, line_idx + 1, false)
  if #lines == 0 then
    return
  end
  -- Use extmark to hide the entire line content
  vim.api.nvim_buf_set_extmark(bufnr, ns_id, line_idx, 0, {
    end_col = #lines[1],
    conceal = "",
  })
end

-- Process marker lines in buffer and create folds/extmarks
-- Scans for markers and applies corresponding UI elements:
-- - fold_start/fold_end: creates manual folds
-- - tool_success: highlights header line with OkMsg
-- - anya__message: displays time or agent info, creates message folds
-- @param bufnr number: Buffer number to process
function M._process_markers(bufnr)
  -- Clear existing extmarks to avoid duplicates
  vim.api.nvim_buf_clear_namespace(bufnr, ns_id, 0, -1)

  local lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)
  local fold_start_line = nil
  -- Track message fold starts by id: { [id] = line_number }
  local message_fold_starts = {}

  for i, line in ipairs(lines) do
    -- Check for conversation markers (hide them)
    if markers.is_conversation_marker(line) then
      M._hide_line(bufnr, i)
    -- Check for message markers (different pattern)
    elseif markers.is_message_marker(line) then
      -- Hide the marker line
      M._hide_line(bufnr, i)
      local msg_info = markers.parse_message_marker(line)
      if msg_info then
        if msg_info.type == "start" then
          -- Apply extmark to the line above the marker (the header line)
          M._apply_message_info(bufnr, i - 1, msg_info)
          -- Record fold start: the header line (line above marker)
          message_fold_starts[msg_info.id] = i - 1
        elseif msg_info.type == "end" then
          -- Create fold from start to this end marker (open by default)
          local start_line = message_fold_starts[msg_info.id]
          if start_line and i > start_line then
            M._create_fold_range(bufnr, start_line, i, true)
          end
          message_fold_starts[msg_info.id] = nil
        end
      end
    elseif markers.is_marker_line(line) then
      -- Hide the marker line
      M._hide_line(bufnr, i)
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
          elseif marker_name == markers.tool_success then
            -- Highlight the header line (line above marker) with checkmark icon
            M._apply_header_highlight(bufnr, i - 1, "AnyaToolSuccess", icons.success)
          elseif marker_name == markers.tool_failure then
            -- Highlight the header line (line above marker) with X icon
            M._apply_header_highlight(bufnr, i - 1, "AnyaToolFailure", icons.failure)
          elseif marker_name == markers.tool_pending then
            -- Highlight the header line (line above marker) with pending icon
            M._apply_header_highlight(bufnr, i - 1, "AnyaToolPending", icons.pending)
          elseif marker_name == markers.thinking then
            -- Highlight the header line (line above marker) with thinking icon
            M._apply_header_highlight(bufnr, i - 1, "AnyaThinking", icons.thinking)
          elseif
            marker_name == markers.edit_pending
            or marker_name == markers.edit_applied
            or marker_name == markers.edit_rejected
            or marker_name == markers.edit_failed
          then
            -- Parse diff info from header line (line above marker)
            local header_line_idx = i - 2 -- 0-indexed, line above marker
            if header_line_idx >= 0 then
              local header_line = lines[i - 1] -- 1-indexed
              local diff_info = parse_edit_header(header_line)
              -- Map marker name to state
              local state = marker_name:match("^edit_(.+)$") or "pending"
              M._apply_edit_header(bufnr, i - 1, state, diff_info)
            end
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
-- @param open boolean|nil: If true, open the fold after creating it (default: false)
function M._create_fold_range(bufnr, start_line, end_line, open)
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
        -- Open the fold if requested
        if open then
          ---@diagnostic disable-next-line: param-type-mismatch
          pcall(vim.cmd, string.format("%dfoldopen", start_line))
        end
      end)

      -- Restore cursor position
      pcall(vim.api.nvim_win_set_cursor, win, cursor)

      -- Only need to create fold in one window
      break
    end
  end
end

-- Apply highlight and icon to a header line using extmarks
-- @param bufnr number: Buffer number
-- @param line_num number: Line number to highlight (1-indexed)
-- @param hl_group string: Highlight group to apply
-- @param icon string|nil: Optional icon to display at right edge of window
function M._apply_header_highlight(bufnr, line_num, hl_group, icon)
  if line_num < 1 then
    return
  end

  -- Convert to 0-indexed for API
  local line_idx = line_num - 1

  -- Get the line content to determine end column
  local lines = vim.api.nvim_buf_get_lines(bufnr, line_idx, line_idx + 1, false)
  if #lines == 0 then
    return
  end

  local line_content = lines[1]

  -- Build extmark options
  local opts = {
    end_col = #line_content,
    hl_group = hl_group,
  }

  -- Add right-aligned icon if provided
  if icon then
    opts.virt_text = { { " " .. icon .. " ", hl_group } }
    opts.virt_text_pos = "right_align"
    opts.hl_mode = "combine" -- Combine with underlying highlights (e.g., fold background)
  end

  vim.api.nvim_buf_set_extmark(bufnr, ns_id, line_idx, 0, opts)
end

-- Build virtual text for edit tool widget (right-aligned)
-- Format: "1: accept | 2: reject [icon]"
-- @param state string: "pending", "applied", or "rejected"
-- @return table: Array of {text, hl_group} tuples for virt_text
local function build_edit_virt_text(state)
  local virt_text = {}

  -- Widget: "1: accept | 2: reject"
  local accept_hl = state == "applied" and "AnyaEditWidgetBold" or "AnyaEditWidget"
  local reject_hl = state == "rejected" and "AnyaEditWidgetBold" or "AnyaEditWidget"

  table.insert(virt_text, { "1: ", "AnyaEditWidget" })
  table.insert(virt_text, { "accept", accept_hl })
  table.insert(virt_text, { " | ", "AnyaEditWidget" })
  table.insert(virt_text, { "2: ", "AnyaEditWidget" })
  table.insert(virt_text, { "reject ", reject_hl })

  -- Icon based on state
  local icon, icon_hl
  if state == "applied" then
    icon = icons.success
    icon_hl = "AnyaToolSuccess"
  elseif state == "rejected" then
    icon = icons.failure
    icon_hl = "AnyaToolFailure"
  else
    icon = icons.pending
    icon_hl = "AnyaToolPending"
  end
  table.insert(virt_text, { icon .. " ", icon_hl })

  return virt_text
end

-- Apply inline highlights to edit header line for diff indicators and filename
-- Format: "27+ 2~ 30- | README.md"
-- @param bufnr number: Buffer number
-- @param line_idx number: Line index (0-indexed)
-- @param line_content string: The header line content
-- @param diff_info table: Parsed diff info (used for validation)
local function apply_edit_header_highlights(bufnr, line_idx, line_content, diff_info)
  -- Highlight diff indicators: "27+" "2~" "30-"
  for start_pos, _, indicator, end_pos in line_content:gmatch("()(%d+)([+~-])()") do
    local hl_group
    if indicator == "+" then
      hl_group = "AnyaEditAdd"
    elseif indicator == "~" then
      hl_group = "AnyaEditChange"
    elseif indicator == "-" then
      hl_group = "AnyaEditDelete"
    end

    if hl_group then
      vim.api.nvim_buf_set_extmark(bufnr, ns_id, line_idx, start_pos - 1, {
        end_col = end_pos - 1,
        hl_group = hl_group,
        hl_mode = "combine",
      })
    end
  end

  -- Highlight filename after "|"
  local pipe_pos = line_content:find("|")
  if pipe_pos and diff_info.filename then
    local filename_start = line_content:find(diff_info.filename, pipe_pos, true)
    if filename_start then
      vim.api.nvim_buf_set_extmark(bufnr, ns_id, line_idx, filename_start - 1, {
        end_col = filename_start - 1 + #diff_info.filename,
        hl_group = "AnyaEditFilename",
        hl_mode = "combine",
      })
    end
  end
end

-- Apply edit tool header with diff info and accept/reject widget
-- @param bufnr number: Buffer number
-- @param line_num number: Line number to highlight (1-indexed)
-- @param state string: "pending", "applied", or "rejected"
-- @param diff_info table: { added = number, changed = number, deleted = number, filename = string }
-- @return number|nil: Extmark ID for later updates
function M._apply_edit_header(bufnr, line_num, state, diff_info)
  if line_num < 1 then
    return nil
  end

  -- Convert to 0-indexed for API
  local line_idx = line_num - 1

  -- Get the line content
  local lines = vim.api.nvim_buf_get_lines(bufnr, line_idx, line_idx + 1, false)
  if #lines == 0 then
    return nil
  end

  local line_content = lines[1]

  -- Apply inline highlights for diff indicators and filename
  apply_edit_header_highlights(bufnr, line_idx, line_content, diff_info)

  -- Build virtual text for widget only
  local virt_text = build_edit_virt_text(state)

  -- Build extmark options for the widget
  local opts = {
    virt_text = virt_text,
    virt_text_pos = "right_align",
    hl_mode = "combine",
  }

  local extmark_id = vim.api.nvim_buf_set_extmark(bufnr, ns_id, line_idx, 0, opts)

  -- Store for later updates
  _G.anya_edit_extmarks[extmark_id] = {
    bufnr = bufnr,
    line_num = line_num,
    state = state,
    diff_info = diff_info,
  }

  return extmark_id
end

-- Update an existing edit extmark's state (called when user presses 1 or 2)
-- @param extmark_id number: The extmark ID to update
-- @param new_state string: "accepted" or "rejected"
function M.update_edit_state(extmark_id, new_state)
  local edit_data = _G.anya_edit_extmarks[extmark_id]
  if not edit_data then
    return
  end

  local bufnr = edit_data.bufnr
  if not vim.api.nvim_buf_is_valid(bufnr) then
    _G.anya_edit_extmarks[extmark_id] = nil
    return
  end

  -- Get current extmark position (it may have moved)
  local extmark = vim.api.nvim_buf_get_extmark_by_id(bufnr, ns_id, extmark_id, {})
  if #extmark == 0 then
    _G.anya_edit_extmarks[extmark_id] = nil
    return
  end

  local line_idx = extmark[1]

  -- Build new virtual text with updated state
  local virt_text = build_edit_virt_text(new_state)

  -- Update the extmark
  vim.api.nvim_buf_set_extmark(bufnr, ns_id, line_idx, 0, {
    id = extmark_id,
    virt_text = virt_text,
    virt_text_pos = "right_align",
    hl_mode = "combine",
  })

  -- Update stored state
  edit_data.state = new_state
end

-- Get edit extmark at a specific line (for keymap handling)
-- @param bufnr number: Buffer number
-- @param line_num number: Line number (1-indexed)
-- @return number|nil: Extmark ID if found
function M.get_edit_extmark_at_line(bufnr, line_num)
  local line_idx = line_num - 1
  local extmarks = vim.api.nvim_buf_get_extmarks(bufnr, ns_id, { line_idx, 0 }, { line_idx, -1 }, {})

  for _, extmark in ipairs(extmarks) do
    local extmark_id = extmark[1]
    if _G.anya_edit_extmarks[extmark_id] then
      return extmark_id
    end
  end

  return nil
end

-- Update edit state at current cursor position (for keymap use)
-- @param new_state string: "accepted" or "rejected"
function M.update_edit_state_at_cursor(new_state)
  local bufnr = vim.api.nvim_get_current_buf()
  local cursor = vim.api.nvim_win_get_cursor(0)
  local line_num = cursor[1]

  local extmark_id = M.get_edit_extmark_at_line(bufnr, line_num)
  if extmark_id then
    M.update_edit_state(extmark_id, new_state)
  end
end

-- Output text synchronously without streaming animation
-- Text is written immediately to the buffer, bypassing the queue
-- @param bufnr number: Buffer number to write to
-- @param text string: Text to output (can contain newlines and marker lines)
-- @param marker_list string[]|nil: List of markers to inject (e.g., {"fold", "tool_success"})
function M.output_sync(bufnr, text, marker_list)
  -- Validate buffer
  if not vim.api.nvim_buf_is_valid(bufnr) then
    return
  end

  local final_text = text

  -- Inject markers if requested
  if marker_list and #marker_list > 0 then
    final_text = M._inject_markers(text, marker_list)
  end

  -- Write all text at once
  M._append_to_buffer(bufnr, final_text)

  -- Process markers and create folds
  M._process_markers(bufnr)

  -- Autoscroll to bottom
  M._autoscroll_to_bottom(bufnr)
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
