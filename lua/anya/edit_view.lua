-- lua/anya/edit_view.lua
-- Renders and manages SEARCH/REPLACE edit blocks in the chat buffer

local M = {}

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
local ICON_PENDING = "○"
local ICON_APPLIED = ""
local ICON_REJECTED = ""
local ICON_FAILED = ""

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

  -- Control button highlights
  vim.api.nvim_set_hl(0, HL_ACCEPT, { fg = ok_hl.fg, bg = visual_hl.bg })
  vim.api.nvim_set_hl(0, HL_REJECT, { fg = err_hl.fg, bg = visual_hl.bg })
  vim.api.nvim_set_hl(0, HL_PENDING, { fg = normal_hl.fg, bg = visual_hl.bg })

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
  local markers = require("anya.markers")
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

-- Handle keypress for apply/reject
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
      local end_row = mark[4].end_row

      if end_row and row >= start_row and row <= end_row then
        local current_state = edit_registry[id].state

        -- Only allow action on pending edits
        if current_state ~= STATE_PENDING then
          vim.notify("Edit already processed", vim.log.levels.INFO)
          return true
        end

        local action = nil
        local success = false
        local message = ""

        if key == "1" then
          -- Apply the edit
          local apply_result = vim.fn.AnyaApplyEditContent(edit_registry[id].raw_block)
          if apply_result and apply_result.success then
            edit_registry[id].state = STATE_APPLIED
            success = true
            message = apply_result.message or ""
            action = "apply"
          else
            edit_registry[id].state = STATE_FAILED
            success = false
            message = apply_result and apply_result.message or "Unknown error"
            action = "failed"
          end
        elseif key == "2" then
          edit_registry[id].state = STATE_REJECTED
          success = true
          action = "reject"
        else
          return false
        end

        update_edit_header(bufnr, id)

        -- Call decision callback if set (for edit tool polling)
        if decision_callback then
          decision_callback(action, success, message)
          decision_callback = nil
        end

        return true
      end
    end
  end

  return false
end

-- Render a SEARCH/REPLACE edit block in the buffer
function M.render_edit(bufnr, filename, search_content, replace_content, raw_block)
  local markers = require("anya.markers")

  local line_count = vim.api.nvim_buf_line_count(bufnr)

  -- Start on the next line (no blank line before)
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
  local fold_end_row = start_line + #block_lines - 2
  require("anya.text")._hide_line(bufnr, fold_end_row + 1) -- 1-indexed

  local end_row = start_line + #block_lines - 1

  -- Create extmark for tracking
  local virt_text = get_header_virt_text(STATE_PENDING)

  local id = vim.api.nvim_buf_set_extmark(bufnr, ns_id, header_row, 0, {
    virt_text = virt_text,
    virt_text_pos = "right_align",
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

return M
