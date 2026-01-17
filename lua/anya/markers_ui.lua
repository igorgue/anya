-- Marker processing and UI utilities for Anya plugin
-- Handles folds, extmarks, edit widgets, and marker logic

local M = {}
local markers = require("anya.markers")
local ui_utils = require("anya.ui_utils")

-- Track edit extmarks for state updates: { [extmark_id] = { bufnr, line_num, state, diff_info } }
if not _G.anya_edit_extmarks then
  _G.anya_edit_extmarks = {}
end

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

-- Calculate duration between two ISO 8601 timestamps
-- @param start_timestamp string ISO 8601 UTC timestamp
-- @param end_timestamp string ISO 8601 UTC timestamp
-- @return string|nil Formatted duration string (e.g., "13.4s", "1m23.5s")
function M._calculate_duration(start_timestamp, end_timestamp)
  -- Parse ISO 8601: YYYY-MM-DDTHH:MM:SS.sssZ (or YYYY-MM-DDTHH:MM:SSZ for backwards compatibility)
  local function parse_iso8601(ts)
    if not ts then
      return nil
    end
    if type(ts) ~= "string" then
      ts = tostring(ts)
    end
    if not ts or ts == "" then
      return nil
    end
    local y, mo, d, h, mi, s, frac = ts:match("(%d+)-(%d+)-(%d+)T(%d+):(%d+):(%d+)%.?(%d*)")
    if not y then
      return nil
    end
    y, mo, d, h, mi, s = tonumber(y), tonumber(mo), tonumber(d), tonumber(h), tonumber(mi), tonumber(s)
    if not (y and mo and d and h and mi and s) then
      return nil
    end
    local base = os.time({ year = y, month = mo, day = d, hour = h, min = mi, sec = s, isdst = false })
    local frac_secs = 0
    if frac and #frac > 0 then
      local padded = frac .. string.rep("0", 6 - #frac) -- pad to microseconds
      frac_secs = tonumber("0." .. padded)
    end
    return base + frac_secs
  end

  local start_sec = parse_iso8601(start_timestamp)
  local end_sec = parse_iso8601(end_timestamp)
  if not start_sec or not end_sec then
    return nil
  end
  local duration_seconds = end_sec - start_sec
  if duration_seconds < 0 then
    return nil
  end
  -- Format duration
  if duration_seconds >= 60 then
    local minutes = math.floor(duration_seconds / 60)
    local seconds = duration_seconds % 60
    return string.format("%dm%.1fs", minutes, seconds)
  else
    return string.format("%.1fs", duration_seconds)
  end
end

-- Apply message info extmark (right-aligned virtual text)
-- For user messages: displays local time (e.g., "2:30pm")
-- For agent messages: displays "<agent> | <model>"
-- @param bufnr number: Buffer number
-- @param line_num number: Line number to apply extmark to (1-indexed)
-- @param meta table: Message metadata from the database
function M._apply_message_info(bufnr, line_num, meta, end_line_num)
  if line_num < 1 or not meta then
    return
  end

  local function as_string(value)
    if value == nil or value == vim.NIL then
      return nil
    end
    if type(value) ~= "string" then
      value = tostring(value)
    end
    if value == "" then
      return nil
    end
    return value
  end

  local line_idx = line_num - 1
  local display_text = ""

  local is_agent = meta.role == "assistant"

  if is_agent then
    local agent_label = as_string(meta.author) or "assistant"
    local model_label = as_string(meta.model) or "unknown"
    display_text = agent_label .. " | " .. model_label
  else
    local start_ts = as_string(meta.created_at)
    if start_ts then
      display_text = markers.utc_to_local_time(start_ts)
    end
  end

  if display_text == "" then
    return
  end

  vim.api.nvim_buf_set_extmark(bufnr, ui_utils.ns_id, line_idx, 0, {
    virt_text = { { display_text, "AnyaToolSuccess" } },
    virt_text_pos = "right_align",
    hl_mode = "combine",
    virt_text_hide = true,
  })

  -- For agent messages, add duration at the end of the last line
  if is_agent and end_line_num and end_line_num > line_num then
    local start_ts = as_string(meta.created_at)
    local end_ts = as_string(meta.ended_at)
    if start_ts and end_ts then
      local duration = M._calculate_duration(start_ts, end_ts)
      if duration then
        -- Find the last non-marker line within the message range
        -- (marker lines are hidden, so we need to find a visible line)
        local all_lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)
        local last_visible_line = end_line_num
        for i = end_line_num, line_num, -1 do
          if i >= 1 and i <= #all_lines then
            local line = all_lines[i]
            -- Skip marker lines and message markers
            if not markers.is_marker_line(line) and not markers.is_message_marker(line) then
              -- Also skip empty lines to avoid blank lines before duration
              if line:match("%S") then
                last_visible_line = i
                break
              end
            end
          end
        end

        -- Only place duration if we found a visible line
        if last_visible_line >= line_num then
          local end_line_idx = last_visible_line - 1
          local lines = vim.api.nvim_buf_get_lines(bufnr, end_line_idx, end_line_idx + 1, false)
          if #lines > 0 then
            local line_content = lines[1]
            vim.api.nvim_buf_set_extmark(bufnr, ui_utils.ns_id, end_line_idx, #line_content, {
              virt_text = { { duration .. " 󰾩  ", "Comment" } },
              virt_text_pos = "eol",
              hl_mode = "combine",
            })
          end
        end
      end
    end
  end
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
  vim.api.nvim_buf_set_extmark(bufnr, ui_utils.ns_id, line_idx, 0, {
    end_col = #lines[1],
    conceal = "",
  })
end

-- Hide a marker line and show duration at the end
-- @param bufnr number: Buffer number
-- @param line_num number: Line number to hide (1-indexed)
-- @param duration string: Duration string to show
function M._hide_line_with_duration(bufnr, line_num, duration)
  if line_num < 1 then
    return
  end
  local line_idx = line_num - 1
  local lines = vim.api.nvim_buf_get_lines(bufnr, line_idx, line_idx + 1, false)
  if #lines == 0 then
    return
  end
  -- Use extmark to hide the entire line content and add duration at the end
  vim.api.nvim_buf_set_extmark(bufnr, ui_utils.ns_id, line_idx, 0, {
    end_col = #lines[1],
    conceal = "",
  })
  -- Add duration as right-aligned text after the concealed marker
  vim.api.nvim_buf_set_extmark(bufnr, ui_utils.ns_id, line_idx, #lines[1], {
    virt_text = { { duration .. " 󰾩  ", "Comment" } },
    virt_text_pos = "right_align",
    hl_mode = "combine",
    virt_text_hide = true,
  })
end

-- Clear all manual folds in a buffer
-- @param bufnr number: Buffer number
function M._clear_folds(bufnr)
  for _, win in ipairs(vim.api.nvim_list_wins()) do
    if vim.api.nvim_win_get_buf(win) == bufnr then
      vim.api.nvim_win_call(win, function()
        pcall(vim.cmd, "normal! zE")
      end)
    end
  end
end

-- Apply highlights to edit block content (SEARCH/REPLACE sections)
-- Scans from header line to find and highlight the search/replace content
-- @param bufnr number: Buffer number
-- @param header_line_num number: Line number of header (1-indexed)
-- @param lines table: All buffer lines (for scanning)
local function apply_edit_content_highlights(bufnr, header_line_num, lines)
  -- Scan forward from header to find edit block structure
  local in_search = false
  local in_replace = false
  local found_start = false

  for i = header_line_num + 1, #lines do
    local line = lines[i]

    -- Check for fold_end marker (end of edit block)
    if markers.is_marker_line(line) then
      local found_markers = markers.parse_marker(line)
      if found_markers then
        for _, marker_name in ipairs(found_markers) do
          if marker_name == markers.fold_end then
            return -- Done with this edit block
          end
        end
      end
    end

    -- Check for SEARCH/REPLACE markers
    if line:match("^<<<<<<< SEARCH") then
      vim.api.nvim_buf_set_extmark(bufnr, ui_utils.ns_id, i - 1, 0, {
        line_hl_group = ui_utils.HL_MARKER,
      })
      in_search = true
      in_replace = false
      found_start = true
    elseif line:match("^=======") and found_start then
      vim.api.nvim_buf_set_extmark(bufnr, ui_utils.ns_id, i - 1, 0, {
        line_hl_group = ui_utils.HL_DIVIDER,
      })
      in_search = false
      in_replace = true
    elseif line:match("^>>>>>>> REPLACE") then
      vim.api.nvim_buf_set_extmark(bufnr, ui_utils.ns_id, i - 1, 0, {
        line_hl_group = ui_utils.HL_MARKER,
      })
      in_replace = false
    elseif in_search then
      vim.api.nvim_buf_set_extmark(bufnr, ui_utils.ns_id, i - 1, 0, {
        line_hl_group = ui_utils.HL_SEARCH,
      })
    elseif in_replace then
      vim.api.nvim_buf_set_extmark(bufnr, ui_utils.ns_id, i - 1, 0, {
        line_hl_group = ui_utils.HL_REPLACE,
      })
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
    opts.virt_text_hide = true -- Hide when line is inside a closed fold (prevents duplicate icons)
  end

  vim.api.nvim_buf_set_extmark(bufnr, ui_utils.ns_id, line_idx, 0, opts)
end

-- Build virtual text for edit tool widget (right-aligned)
-- Format: "1: accept | 2: reject [icon]"
-- @param state string: "pending", "applied", "rejected", or "failed"
-- @return table: Array of {text, hl_group} tuples for virt_text
local function build_edit_virt_text(state)
  local virt_text = {}

  -- Widget: "1: accept | 2: reject"
  -- Use green for accept when applied, red for reject when rejected/failed, gray otherwise
  local accept_hl = state == "applied" and "AnyaEditAccept" or "AnyaEditPending"
  local reject_hl = (state == "rejected" or state == "failed") and "AnyaEditReject" or "AnyaEditPending"

  table.insert(virt_text, { "1: ", "AnyaEditPending" })
  table.insert(virt_text, { "accept", accept_hl })
  table.insert(virt_text, { " | ", "AnyaEditPending" })
  table.insert(virt_text, { "2: ", "AnyaEditPending" })
  table.insert(virt_text, { "reject ", reject_hl })

  -- Icon based on state
  local icon, icon_hl
  if state == "applied" then
    icon = ui_utils.icons.success
    icon_hl = "AnyaEditAccept"
  elseif state == "rejected" or state == "failed" then
    icon = ui_utils.icons.failure
    icon_hl = "AnyaEditReject"
  else
    icon = ui_utils.icons.pending
    icon_hl = "AnyaEditPending"
  end
  table.insert(virt_text, { icon .. " ", icon_hl })

  return virt_text
end

-- Apply inline highlights to edit header line for diff indicators and filename
-- Format: "+2 -1 | README.md"
-- @param bufnr number: Buffer number
-- @param line_idx number: Line index (0-indexed)
-- @param line_content string: The header line content
-- @param diff_info table: Parsed diff info (used for validation)
local function apply_edit_header_highlights(bufnr, line_idx, line_content, diff_info)
  -- Highlight diff indicators: "+2" "-1" (indicator before number)
  for start_pos, indicator, _, end_pos in line_content:gmatch("()([+~-])(%d+)()") do
    local hl_group
    if indicator == "+" then
      hl_group = "AnyaEditAdd"
    elseif indicator == "~" then
      hl_group = "AnyaEditChange"
    elseif indicator == "-" then
      hl_group = "AnyaEditDelete"
    end

    if hl_group then
      vim.api.nvim_buf_set_extmark(bufnr, ui_utils.ns_id, line_idx, start_pos - 1, {
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
      vim.api.nvim_buf_set_extmark(bufnr, ui_utils.ns_id, line_idx, filename_start - 1, {
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
    virt_text_hide = true,
  }

  local extmark_id = vim.api.nvim_buf_set_extmark(bufnr, ui_utils.edit_view_ns_id, line_idx, 0, opts)

  -- Store for later updates
  _G.anya_edit_extmarks[extmark_id] = {
    bufnr = bufnr,
    line_num = line_num,
    state = state,
    diff_info = diff_info,
  }

  return extmark_id
end

-- Process marker lines in buffer and create folds/extmarks
-- Scans for markers and applies corresponding UI elements:
-- - fold_start/fold_end: creates manual folds
-- - tool_success: highlights header line with OkMsg
-- - am: displays time or agent info, creates message folds
-- @param bufnr number: Buffer number to process
function M._process_markers(bufnr)
  -- Clear existing extmarks to avoid duplicates
  vim.api.nvim_buf_clear_namespace(bufnr, ui_utils.ns_id, 0, -1)

  -- Clear existing folds to avoid duplicates
  M._clear_folds(bufnr)

  local lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)
  local fold_start_line = nil
  local fold_is_edit = false -- Track if current fold is an edit (should be open)
  local message_markers = {}

  local conv_id = nil
  local ok_conv, conv_var = pcall(vim.api.nvim_buf_get_var, bufnr, "anya_conversation_id")
  if ok_conv then
    conv_id = conv_var
  end

  local message_lookup = {}
  if conv_id then
    local ok_data, data = pcall(vim.fn.AnyaLoadConversation, conv_id)
    if ok_data and data and data.messages then
      for _, msg in ipairs(data.messages) do
        message_lookup[msg.id] = msg
      end
    end
  end

  local function get_message_meta(id)
    return message_lookup[id]
  end

  local thinking_content_start ---@type integer|nil -- 1-indexed

  for i, line in ipairs(lines) do
    if markers.is_message_marker(line) then
      local msg_info = markers.parse_message_marker(line)
      if msg_info and msg_info.id then
        table.insert(message_markers, { id = msg_info.id, line = i })
        M._hide_line(bufnr, i)
      end
    elseif markers.is_tool_output_marker(line) then
       -- Tool output reference marker - hide it and add virtual text to line above
       local info = markers.parse_tool_output_marker(line)
       if info then
         -- Hide the entire ato: marker line
         M._hide_line(bufnr, i)

         -- Check if line above has at: marker (the line we want to attach virtual text to)
         if i > 1 then
           local above_line = lines[i - 1]
           local at_marker_idx = above_line:find("<!%-%- at:")

           -- If above line has at: marker, attach virtual text there
           if at_marker_idx then
             local line_count = info.line_count or 0
             -- Add virtual text at end of above line
             vim.api.nvim_buf_set_extmark(bufnr, ui_utils.ns_id, i - 2, #above_line, {
               virt_text = {
                 { "  " .. ui_utils.icons.success .. " ", "AnyaToolSuccess" },
                 { ui_utils.icons.tool_output .. " View output", "Comment" },
                 { " (" .. line_count .. " lines)", "NonText" },
               },
               virt_text_pos = "eol",
               hl_mode = "combine",
             })
           end
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
            fold_is_edit = false -- Reset, will be set if edit marker found
          elseif marker_name == markers.fold_end then
            -- fold_end line is included in the fold
            local fold_end_line = i
            if fold_start_line and fold_end_line > fold_start_line then
              -- Open edit folds so user can see content and decide
              M._create_fold_range(bufnr, fold_start_line, fold_end_line, fold_is_edit)
            end

            -- If this fold was a thinking block, highlight its content as Comment.
            if thinking_content_start and fold_end_line > thinking_content_start then
              for lnum = thinking_content_start, fold_end_line - 1 do
                vim.api.nvim_buf_set_extmark(bufnr, ui_utils.ns_id, lnum - 1, 0, {
                  line_hl_group = "Comment",
                })
              end
            end

            fold_start_line = nil
            fold_is_edit = false
            thinking_content_start = nil
          elseif marker_name == markers.tool_success then
            -- Highlight the header line (line above marker) with checkmark icon
            M._apply_header_highlight(bufnr, i - 1, "AnyaToolSuccess", ui_utils.icons.success)
          elseif marker_name == markers.tool_failure then
            -- Highlight the header line (line above marker) with X icon
            M._apply_header_highlight(bufnr, i - 1, "AnyaToolFailure", ui_utils.icons.failure)
          elseif marker_name == markers.tool_pending then
            -- Highlight the header line (line above marker) with pending icon
            M._apply_header_highlight(bufnr, i - 1, "AnyaToolPending", ui_utils.icons.pending)
          elseif marker_name == markers.thinking then
            -- Thinking block: treat like a fold with brain icon
            fold_start_line = i - 1
            fold_is_edit = false
            thinking_content_start = i + 1
            -- Highlight header with thinking text color and brain icon
            M._apply_header_highlight(bufnr, i - 1, "AnyaThinking", ui_utils.icons.thinking)
          elseif
            marker_name == markers.edit_pending
            or marker_name == markers.edit_applied
            or marker_name == markers.edit_rejected
            or marker_name == markers.edit_failed
          then
            -- Mark this fold as an edit fold (should be open for pending)
            if marker_name == markers.edit_pending then
              fold_is_edit = true
            end
            -- Parse diff info from header line (line above marker)
            local header_line_idx = i - 2 -- 0-indexed, line above marker
            if header_line_idx >= 0 then
              -- Skip if edit_view already has an extmark on this line
              local existing = vim.api.nvim_buf_get_extmarks(
                bufnr,
                ui_utils.edit_view_ns_id,
                { header_line_idx, 0 },
                { header_line_idx, -1 },
                {}
              )
              if #existing == 0 then
                local header_line = lines[i - 1] -- 1-indexed
                local diff_info = parse_edit_header(header_line)
                -- Map marker name to state
                local state = marker_name:match("^edit_(.+)$") or "pending"
                M._apply_edit_header(bufnr, i - 1, state, diff_info)
                -- Apply content highlights for SEARCH/REPLACE sections
                apply_edit_content_highlights(bufnr, i - 1, lines)
              end
            end
          end
        end
      end
    end
  end

  for idx, msg_marker in ipairs(message_markers) do
    local start_line = msg_marker.line
    local next_marker = message_markers[idx + 1]
    local end_line = next_marker and (next_marker.line - 1) or #lines
    if end_line < start_line then
      end_line = start_line
    end

    -- Check if this message contains any tool folds (fold_start markers)
    -- If so, skip creating message fold to avoid double folding
    local has_tool_fold = false
    for j = start_line, end_line do
      if j <= #lines then
        local check_line = lines[j]
        if markers.is_marker_line(check_line) and markers.has_marker(check_line, markers.fold_start) then
          has_tool_fold = true
          break
        end
      end
    end

    -- Only create message fold if there are no nested tool folds
    if not has_tool_fold then
      M._create_fold_range(bufnr, start_line, end_line, true)
    end

    local meta = get_message_meta(msg_marker.id)
    if meta then
      local header_line = start_line
      M._apply_message_info(bufnr, header_line, meta, end_line)
    end
  end
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
  local extmark = vim.api.nvim_buf_get_extmark_by_id(bufnr, ui_utils.ns_id, extmark_id, {})
  if #extmark == 0 then
    _G.anya_edit_extmarks[extmark_id] = nil
    return
  end

  local line_idx = extmark[1]

  -- Build new virtual text with updated state
  local virt_text = build_edit_virt_text(new_state)

  -- Update the extmark
  vim.api.nvim_buf_set_extmark(bufnr, ui_utils.ns_id, line_idx, 0, {
    id = extmark_id,
    virt_text = virt_text,
    virt_text_pos = "right_align",
    hl_mode = "combine",
    virt_text_hide = true,
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
  local extmarks = vim.api.nvim_buf_get_extmarks(bufnr, ui_utils.ns_id, { line_idx, 0 }, { line_idx, -1 }, {})

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

return M
