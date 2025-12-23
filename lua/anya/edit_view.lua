-- lua/anya/edit_view.lua
-- Renders and manages SEARCH/REPLACE edit blocks in the chat buffer

local M = {}
local markers = require("anya.markers")

-- Namespace for edit block highlights and virtual text
local ns_id = vim.api.nvim_create_namespace("anya_edit_view")

-- Decision callback - called when user makes a choice
local decision_callback = nil

-- State constants
local STATE_PENDING = 0
local STATE_APPLIED = 1
local STATE_REJECTED = 2
local STATE_FAILED = 3

-- Icons
-- NOTE: Do not change these lines, LLMs can't deal with nerdfonts.
-- so don't change them please!
local ICON_PENDING = ""
local ICON_APPLIED = ""
local ICON_REJECTED = ""
local ICON_FAILED = "󰗖"

-- Highlight group names
local HL_ACCEPT = "AnyaEditAccept"
local HL_REJECT = "AnyaEditReject"
local HL_PENDING = "AnyaEditPending"
local HL_SEARCH = "AnyaEditSearch"
local HL_REPLACE = "AnyaEditReplace"
local HL_MARKER = "AnyaEditMarker"
local HL_DIVIDER = "AnyaEditDivider"
local HL_FILENAME = "AnyaEditFilename"

-- Setup highlight groups
local function setup_highlights()
  local ok_hl = vim.api.nvim_get_hl(0, { name = "DiagnosticOk", link = false })
  if not ok_hl.fg then
    ok_hl = vim.api.nvim_get_hl(0, { name = "String", link = false })
  end

  local err_hl = vim.api.nvim_get_hl(0, { name = "ErrorMsg", link = false })
  local normal_hl = vim.api.nvim_get_hl(0, { name = "Normal", link = false })
  local visual_hl = vim.api.nvim_get_hl(0, { name = "Visual", link = false })
  local diff_del = vim.api.nvim_get_hl(0, { name = "DiffDelete", link = false })
  local diff_add = vim.api.nvim_get_hl(0, { name = "DiffAdd", link = false })
  local comment_hl = vim.api.nvim_get_hl(0, { name = "Comment", link = false })
  local directory_hl = vim.api.nvim_get_hl(0, { name = "Directory", link = false })

  -- Control button highlights (transparent bg)
  vim.api.nvim_set_hl(0, HL_ACCEPT, { fg = ok_hl.fg })
  vim.api.nvim_set_hl(0, HL_REJECT, { fg = err_hl.fg })
  vim.api.nvim_set_hl(0, HL_PENDING, { fg = comment_hl.fg })

  -- Search/Replace content highlights
  vim.api.nvim_set_hl(0, HL_SEARCH, { bg = diff_del.bg, fg = diff_del.fg })
  vim.api.nvim_set_hl(0, HL_REPLACE, { bg = diff_add.bg, fg = diff_add.fg })

  -- Marker highlights
  vim.api.nvim_set_hl(0, HL_MARKER, { fg = comment_hl.fg, bold = true })
  vim.api.nvim_set_hl(0, HL_DIVIDER, { fg = comment_hl.fg })

  -- Filename highlight
  vim.api.nvim_set_hl(0, HL_FILENAME, { fg = directory_hl.fg, bold = true })
end

setup_highlights()

-- Store edit data by extmark id
local edit_registry = {}

-- Parse an edit block to extract stats
local function parse_edit_stats(search_content, replace_content)
  local search_lines = vim.split(search_content or "", "\n")
  local replace_lines = vim.split(replace_content or "", "\n")

  -- Remove trailing empty lines
  while #search_lines > 0 and search_lines[#search_lines] == "" do
    table.remove(search_lines)
  end
  while #replace_lines > 0 and replace_lines[#replace_lines] == "" do
    table.remove(replace_lines)
  end

  return #replace_lines, #search_lines
end

-- Get virtual text for the header based on state
local function get_header_virt_text(state)
  local virt_text = {}

  local icon = ICON_PENDING
  local icon_hl = HL_PENDING

  if state == STATE_APPLIED then
    icon = ICON_APPLIED
    icon_hl = HL_ACCEPT
  elseif state == STATE_REJECTED then
    icon = ICON_REJECTED
    icon_hl = HL_REJECT
  elseif state == STATE_FAILED then
    icon = ICON_FAILED
    icon_hl = HL_REJECT
  end

  -- Controls
  local accept_hl = state == STATE_APPLIED and HL_ACCEPT or HL_PENDING
  local reject_hl = (state == STATE_REJECTED or state == STATE_FAILED) and HL_REJECT or HL_PENDING

  table.insert(virt_text, { "1: ", HL_PENDING })
  table.insert(virt_text, { "apply", accept_hl })
  table.insert(virt_text, { " | ", HL_PENDING })
  table.insert(virt_text, { "2: ", HL_PENDING })
  table.insert(virt_text, { "reject", reject_hl })
  table.insert(virt_text, { " " .. icon .. " ", icon_hl })

  return virt_text
end

-- Update the header extmark and marker
local function update_edit_header(bufnr, extmark_id)
  local edit_data = edit_registry[extmark_id]
  if not edit_data then
    return
  end

  local extmark = vim.api.nvim_buf_get_extmark_by_id(bufnr, ns_id, extmark_id, { details = true })
  if not extmark or #extmark == 0 then
    return
  end

  local row = extmark[1]
  local virt_text = get_header_virt_text(edit_data.state)

  vim.api.nvim_buf_set_extmark(bufnr, ns_id, row, 0, {
    id = extmark_id,
    virt_text = virt_text,
    virt_text_pos = "right_align",
    hl_mode = "combine",
    end_row = edit_data.end_row,
  })

  -- Update the marker in the buffer to reflect the new state
  local marker_row = row + 1 -- Marker is on the line after header
  local new_marker_name
  if edit_data.state == STATE_APPLIED then
    new_marker_name = markers.edit_applied
  elseif edit_data.state == STATE_REJECTED then
    new_marker_name = markers.edit_rejected
  elseif edit_data.state == STATE_FAILED then
    new_marker_name = markers.edit_failed
  else
    new_marker_name = markers.edit_pending
  end

  local new_marker = markers.make_marker(markers.fold_start, new_marker_name)
  vim.api.nvim_buf_set_lines(bufnr, marker_row, marker_row + 1, false, { new_marker })
end

-- Handle keypress for apply/reject (with toggle support)
function M.handle_keypress(bufnr, key)
  local cursor
  local ok, result = pcall(function()
    return vim.api.nvim_win_get_cursor(0)
  end)

  if not ok then
    local found_win = nil
    for _, win in ipairs(vim.api.nvim_list_wins()) do
      if vim.api.nvim_win_get_buf(win) == bufnr then
        found_win = win
        break
      end
    end

    if not found_win then
      return false
    end

    ok, result = pcall(function()
      return vim.api.nvim_win_get_cursor(found_win)
    end)

    if not ok then
      return false
    end

    cursor = result
  else
    cursor = result
  end

  local row = cursor[1] - 1

  local extmarks = vim.api.nvim_buf_get_extmarks(bufnr, ns_id, 0, -1, { details = true })

  for _, mark in ipairs(extmarks) do
    local id = mark[1]

    if edit_registry[id] then
      local start_row = mark[2]
      local end_row = mark[4].end_row or edit_registry[id].end_row

      if end_row and row >= start_row and row <= end_row then
        local current_state = edit_registry[id].state
        local action = nil
        local success = false
        local message = ""
        local old_marker = nil
        local new_marker = nil

        if key == "1" then
          if current_state == STATE_PENDING then
            -- Apply the edit - schedule asynchronously to avoid RPC channel issues
            vim.schedule(function()
              local ok, apply_result = pcall(vim.fn.AnyaApplyEditContent, edit_registry[id].raw_block)
              if not ok then
                vim.notify(
                  "Plugin connection lost. Please restart Neovim or run :UpdateRemotePlugins",
                  vim.log.levels.ERROR
                )
                edit_registry[id].state = STATE_FAILED
                if decision_callback then
                  decision_callback(
                    "failed",
                    false,
                    "Plugin connection lost. Please restart Neovim or run :UpdateRemotePlugins"
                  )
                  decision_callback = nil
                end
                return
              end

              if apply_result and apply_result.success then
                edit_registry[id].state = STATE_APPLIED
                success = true
                message = apply_result.message or ""
                action = "apply"
                old_marker = markers.edit_pending
                new_marker = markers.edit_applied
              else
                edit_registry[id].state = STATE_FAILED
                success = false
                message = apply_result and apply_result.message or "Failed to apply edit"
                action = "failed"
                old_marker = markers.edit_pending
                new_marker = markers.edit_failed
              end

              update_edit_header(bufnr, id)
              if old_marker and new_marker and old_marker ~= new_marker then
                M.update_marker_in_db(bufnr, old_marker, new_marker)
              end
              if decision_callback then
                decision_callback(action, success, message)
                decision_callback = nil
              end
            end)
            return true
          elseif current_state == STATE_REJECTED then
            -- Toggle from rejected to applied
            vim.schedule(function()
              local ok, apply_result = pcall(vim.fn.AnyaApplyEditContent, edit_registry[id].raw_block)
              if not ok then
                vim.notify(
                  "Plugin connection lost. Please restart Neovim or run :UpdateRemotePlugins",
                  vim.log.levels.ERROR
                )
                edit_registry[id].state = STATE_FAILED
                if decision_callback then
                  decision_callback(
                    "failed",
                    false,
                    "Plugin connection lost. Please restart Neovim or run :UpdateRemotePlugins"
                  )
                  decision_callback = nil
                end
                return
              end

              if apply_result and apply_result.success then
                edit_registry[id].state = STATE_APPLIED
                success = true
                message = apply_result.message or ""
                action = "toggle_apply"
                old_marker = markers.edit_rejected
                new_marker = markers.edit_applied
              else
                edit_registry[id].state = STATE_FAILED
                success = false
                message = apply_result and apply_result.message or "Failed to apply edit"
                action = "failed"
              end

              update_edit_header(bufnr, id)
              if old_marker and new_marker and old_marker ~= new_marker then
                M.update_marker_in_db(bufnr, old_marker, new_marker)
              end
              if decision_callback then
                decision_callback(action, success, message)
                decision_callback = nil
              end
            end)
            return true
          elseif current_state == STATE_APPLIED or current_state == STATE_FAILED then
            vim.notify("Edit already applied", vim.log.levels.INFO)
            return true
          end
        elseif key == "2" then
          if current_state == STATE_PENDING then
            -- Reject the edit
            edit_registry[id].state = STATE_REJECTED
            success = true
            action = "reject"
            old_marker = markers.edit_pending
            new_marker = markers.edit_rejected

            update_edit_header(bufnr, id)
            if old_marker and new_marker and old_marker ~= new_marker then
              M.update_marker_in_db(bufnr, old_marker, new_marker)
            end
            if decision_callback then
              decision_callback(action, success, message)
              decision_callback = nil
            end
            return true
          elseif current_state == STATE_APPLIED then
            -- Toggle from applied to rejected (unapply)
            vim.schedule(function()
              local ok, unapply_result = pcall(vim.fn.AnyaUnapplyEdit, edit_registry[id].raw_block)
              if not ok then
                vim.notify(
                  "Plugin connection lost while unapplying edit. Please restart Neovim or run :UpdateRemotePlugins",
                  vim.log.levels.ERROR
                )
                return
              end

              if unapply_result and unapply_result.success then
                edit_registry[id].state = STATE_REJECTED
                success = true
                message = "Edit unapplied"
                action = "toggle_reject"
                old_marker = markers.edit_applied
                new_marker = markers.edit_rejected
              else
                local err_msg = unapply_result and unapply_result.message or "Failed to unapply"
                vim.notify("Failed to unapply: " .. err_msg, vim.log.levels.ERROR)
                return
              end

              update_edit_header(bufnr, id)
              if old_marker and new_marker and old_marker ~= new_marker then
                M.update_marker_in_db(bufnr, old_marker, new_marker)
              end
              if decision_callback then
                decision_callback(action, success, message)
                decision_callback = nil
              end
            end)
            return true
          elseif current_state == STATE_FAILED then
            -- Can change failed to rejected
            edit_registry[id].state = STATE_REJECTED
            success = true
            action = "toggle_reject"
            old_marker = markers.edit_failed
            new_marker = markers.edit_rejected

            update_edit_header(bufnr, id)
            if old_marker and new_marker and old_marker ~= new_marker then
              M.update_marker_in_db(bufnr, old_marker, new_marker)
            end
            if decision_callback then
              decision_callback(action, success, message)
              decision_callback = nil
            end
            return true
          elseif current_state == STATE_REJECTED then
            vim.notify("Edit already rejected", vim.log.levels.INFO)
            return true
          end
        else
          return false
        end
      end
    end
  end

  return false
end

-- Update the edit marker in the database for the current message
function M.update_marker_in_db(bufnr, old_marker, new_marker, from_line)
  local conversation = require("anya.conversation")
  local message_id = conversation.get_current_message_id(bufnr, from_line)
  if not message_id then
    vim.notify("Warning: Could not find message ID to sync edit state to database", vim.log.levels.WARN)
    return
  end

  local result = vim.fn.AnyaUpdateEditMarker(message_id, old_marker, new_marker)

  if not result or not result.success then
    local err_msg = result and result.message or "Unknown error"
    vim.notify("Error syncing edit marker: " .. err_msg, vim.log.levels.ERROR)
  end
end

-- Render a SEARCH/REPLACE edit block in the buffer
function M.render_edit(bufnr, filename, search_content, replace_content, raw_block)
  local line_count = vim.api.nvim_buf_line_count(bufnr)
  local start_line = line_count

  -- Calculate stats
  local adds, dels = parse_edit_stats(search_content, replace_content)
  local header_text = string.format("+%d -%d | %s", adds, dels, filename)

  -- Build block lines with code fence wrapper and markers
  local block_lines = {}
  table.insert(block_lines, header_text)
  -- Add fold_start marker with edit_pending
  table.insert(block_lines, markers.make_marker(markers.fold_start, markers.edit_pending))
  -- Start code fence to prevent markdown rendering
  table.insert(block_lines, "``````")
  -- Add filename for parsing
  table.insert(block_lines, filename)
  table.insert(block_lines, "<<<<<<< SEARCH")

  local search_lines = vim.split(search_content or "", "\n")
  for _, line in ipairs(search_lines) do
    table.insert(block_lines, line)
  end

  table.insert(block_lines, "=======")

  local replace_lines = vim.split(replace_content or "", "\n")
  for _, line in ipairs(replace_lines) do
    table.insert(block_lines, line)
  end

  table.insert(block_lines, ">>>>>>> REPLACE")
  -- End code fence
  table.insert(block_lines, "``````")
  -- Add fold_end marker
  table.insert(block_lines, markers.make_marker(markers.fold_end))
  local fold_end_index = #block_lines
  -- Add a trailing blank line to separate from following LLM messages
  table.insert(block_lines, "")

  vim.api.nvim_buf_set_lines(bufnr, start_line, -1, false, block_lines)

  -- Apply syntax highlighting
  local header_row = start_line
  local marker_row = start_line + 1
  -- fence_start_row is start_line + 2, current_row starts after fence
  local current_row = start_line + 3

  -- Hide the marker line
  require("anya.text")._hide_line(bufnr, marker_row + 1) -- 1-indexed

  -- Highlight <<<<<<< SEARCH marker
  vim.api.nvim_buf_add_highlight(bufnr, ns_id, HL_MARKER, current_row, 0, -1)
  current_row = current_row + 1

  -- Highlight search content (deletion style)
  for _ = 1, #search_lines do
    vim.api.nvim_buf_add_highlight(bufnr, ns_id, HL_SEARCH, current_row, 0, -1)
    current_row = current_row + 1
  end

  -- Highlight ======= divider
  vim.api.nvim_buf_add_highlight(bufnr, ns_id, HL_DIVIDER, current_row, 0, -1)
  current_row = current_row + 1

  -- Highlight replace content (addition style)
  for _ = 1, #replace_lines do
    vim.api.nvim_buf_add_highlight(bufnr, ns_id, HL_REPLACE, current_row, 0, -1)
    current_row = current_row + 1
  end

  -- Highlight >>>>>>> REPLACE marker
  vim.api.nvim_buf_add_highlight(bufnr, ns_id, HL_MARKER, current_row, 0, -1)

  -- Hide the fold_end marker line
  local fold_end_row = start_line + fold_end_index - 1
  require("anya.text")._hide_line(bufnr, fold_end_row + 1) -- 1-indexed

  local end_row = start_line + #block_lines - 1

  -- Create extmark for tracking
  local virt_text = get_header_virt_text(STATE_PENDING)

  local id = vim.api.nvim_buf_set_extmark(bufnr, ns_id, header_row, 0, {
    virt_text = virt_text,
    virt_text_pos = "right_align",
    hl_mode = "combine",
    end_row = end_row,
  })

  edit_registry[id] = {
    state = STATE_PENDING,
    filename = filename,
    search_content = search_content,
    replace_content = replace_content,
    raw_block = raw_block,
    header_row = header_row,
    end_row = end_row,
  }

  -- Apply header highlights
  local line = header_text
  local s_add, e_add = line:find("%+%d+")
  if s_add then
    vim.api.nvim_buf_add_highlight(bufnr, ns_id, "OkMsg", header_row, s_add - 1, e_add)
  end
  local s_del, e_del = line:find("%-%d+")
  if s_del then
    vim.api.nvim_buf_add_highlight(bufnr, ns_id, "ErrorMsg", header_row, s_del - 1, e_del)
  end
  local s_file = line:find("| (.+)")
  if s_file then
    vim.api.nvim_buf_add_highlight(bufnr, ns_id, HL_FILENAME, header_row, s_file + 1, -1)
  end

  return id
end

-- Rebuild edit registry from buffer extmarks (needed when loading from database)
-- This extracts the raw edit block from the buffer for each edit widget
function M.rebuild_registry(bufnr)
  edit_registry = {}

  local extmarks = vim.api.nvim_buf_get_extmarks(bufnr, ns_id, 0, -1, { details = true })

  for _, mark in ipairs(extmarks) do
    local id = mark[1]
    local start_row = mark[2]
    local end_row = mark[4].end_row

    -- Find the fold_end marker to determine the actual end_row if not in extmark
    if not end_row then
      local lines = vim.api.nvim_buf_get_lines(bufnr, start_row, -1, false)
      for i, line in ipairs(lines) do
        if markers.is_marker_line(line) then
          local found_markers = markers.parse_marker(line)
          if found_markers then
            for _, marker_name in ipairs(found_markers) do
              if marker_name == markers.fold_end then
                end_row = start_row + i - 1
                break
              end
            end
          end
        end
        if end_row then
          break
        end
      end
    end

    if end_row then
      -- Extract raw block from buffer lines
      -- Includes: header, fold_start marker (hidden), code fences, SEARCH/REPLACE
      -- Excludes: fold_end marker (on end_row)
      local lines = vim.api.nvim_buf_get_lines(bufnr, start_row, end_row, false)
      local raw_block = table.concat(lines, "\n")

      -- Determine state from marker on line after header
      local marker_line_idx = start_row + 1
      local marker_lines = vim.api.nvim_buf_get_lines(bufnr, marker_line_idx, marker_line_idx + 1, false)
      local state = STATE_PENDING

      if #marker_lines > 0 then
        local marker_line = marker_lines[1]
        if marker_line:find(markers.edit_applied) then
          state = STATE_APPLIED
        elseif marker_line:find(markers.edit_rejected) then
          state = STATE_REJECTED
        elseif marker_line:find(markers.edit_failed) then
          state = STATE_FAILED
        end
      end

      -- Extract filename from the line after code fence (start_row + 3)
      -- Layout: header(0), marker(1), fence(2), filename(3), <<<<<<< SEARCH(4)
      local filename_line_idx = start_row + 3
      local filename_lines = vim.api.nvim_buf_get_lines(bufnr, filename_line_idx, filename_line_idx + 1, false)
      local filename = ""
      if #filename_lines > 0 then
        filename = vim.trim(filename_lines[1])
      end

      edit_registry[id] = {
        state = state,
        filename = filename,
        raw_block = raw_block,
        header_row = start_row,
        end_row = end_row,
      }
    end
  end
end

-- Setup keymaps for the buffer
function M.setup_keymaps(bufnr)
  local opts = { noremap = true, silent = true, buffer = bufnr }
  vim.keymap.set("n", "1", function()
    if not M.handle_keypress(bufnr, "1") then
      vim.cmd("normal! 1|")
    end
  end, opts)
  vim.keymap.set("n", "2", function()
    if not M.handle_keypress(bufnr, "2") then
      vim.cmd("normal! 2|")
    end
  end, opts)
end

-- Clear all edit blocks from registry
function M.clear_registry()
  edit_registry = {}
end

-- Get namespace id
function M.get_namespace()
  return ns_id
end

-- Set decision callback for edit tool polling
function M.set_decision_callback(callback)
  decision_callback = callback
end

-- Clear decision callback
function M.clear_decision_callback()
  decision_callback = nil
end

-- Handle keypress for any pending edit (used from prompt buffer)
-- Finds the most recent pending edit and applies/rejects it
function M.handle_keypress_any_edit(key)
  -- Find the most recent pending edit by checking registry in reverse order
  local last_pending_id = nil
  for id, data in pairs(edit_registry) do
    if data.state == STATE_PENDING then
      if not last_pending_id or id > last_pending_id then
        last_pending_id = id
      end
    end
  end

  if not last_pending_id then
    return false
  end

  local edit_data = edit_registry[last_pending_id]
  local bufnr = nil

  -- Find the buffer containing this edit (should be chat buffer)
  for _, buf in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_is_valid(buf) then
      local extmark = vim.api.nvim_buf_get_extmark_by_id(buf, ns_id, last_pending_id, { details = true })
      if extmark and #extmark > 0 then
        bufnr = buf
        break
      end
    end
  end

  if not bufnr then
    return false
  end

  -- Now simulate the keypress on that edit
  local action = nil
  local success = false
  local message = ""
  local old_marker = nil
  local new_marker = nil

  if key == "1" then
    local ok, apply_result = pcall(vim.fn.AnyaApplyEditContent, edit_data.raw_block)
    if not ok then
      -- Channel error - plugin likely crashed or restarted
      edit_data.state = STATE_FAILED
      success = false
      message = "Plugin connection lost. Please restart Neovim or run :UpdateRemotePlugins"
      action = "failed"
      old_marker = markers.edit_pending
      new_marker = markers.edit_failed
    elseif apply_result and apply_result.success then
      edit_data.state = STATE_APPLIED
      success = true
      message = apply_result.message or ""
      action = "apply"
      old_marker = markers.edit_pending
      new_marker = markers.edit_applied
    else
      edit_data.state = STATE_FAILED
      success = false
      message = apply_result and apply_result.message or "Failed to apply edit"
      action = "failed"
      old_marker = markers.edit_pending
      new_marker = markers.edit_failed
    end
  elseif key == "2" then
    edit_data.state = STATE_REJECTED
    success = true
    action = "reject"
    old_marker = markers.edit_pending
    new_marker = markers.edit_rejected
  else
    return false
  end

  update_edit_header(bufnr, last_pending_id)

  -- Call decision callback if set (for edit tool polling)
  -- The decision callback will handle database sync
  if decision_callback then
    decision_callback(action, success, message)
    decision_callback = nil
  end

  return true
end

return M
