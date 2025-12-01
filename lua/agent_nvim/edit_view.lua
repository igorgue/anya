-- lua/agent_nvim/edit_view.lua
-- Renders and manages SEARCH/REPLACE edit blocks in the agent buffer

local M = {}

-- Namespace for edit block highlights and virtual text
local ns_id = vim.api.nvim_create_namespace("agent_edit_view")

-- State constants
local STATE_PENDING = 0
local STATE_ACCEPT = 1
local STATE_REJECT = 2

-- Icons
local ICON_PENDING = "○"
local ICON_APPLIED = ""
local ICON_REJECTED = ""

-- Highlight group names
local HL_ACCEPT = "AgentEditAccept"
local HL_REJECT = "AgentEditReject"
local HL_PENDING = "AgentEditPending"
local HL_SEARCH = "AgentEditSearch"
local HL_REPLACE = "AgentEditReplace"
local HL_MARKER = "AgentEditMarker"
local HL_DIVIDER = "AgentEditDivider"
local HL_FILENAME = "AgentEditFilename"

-- Setup highlight groups
local function setup_highlights()
  -- Get colors from existing groups
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

  -- Marker highlights (<<<<<<< SEARCH, =======, >>>>>>> REPLACE)
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

  local deletions = #search_lines
  local additions = #replace_lines

  return additions, deletions
end

-- Get virtual text for the header based on state
local function get_header_virt_text(state, additions, deletions)
  local virt_text = {}

  -- Determine icon and highlight based on state
  local icon = ICON_PENDING
  local icon_hl = HL_PENDING

  if state == STATE_ACCEPT then
    icon = ICON_APPLIED
    icon_hl = HL_ACCEPT
  elseif state == STATE_REJECT then
    icon = ICON_REJECTED
    icon_hl = HL_REJECT
  end

  -- Controls
  local function add_option(opt_state, label, key)
    local hl = HL_PENDING
    if state == opt_state then
      if state == STATE_ACCEPT then
        hl = HL_ACCEPT
      elseif state == STATE_REJECT then
        hl = HL_REJECT
      end
      table.insert(virt_text, { string.format("%s: %s", key, label), hl })
    else
      table.insert(virt_text, { string.format("%s: %s", key, label), HL_PENDING })
    end
    table.insert(virt_text, { " | ", HL_PENDING })
  end

  add_option(STATE_ACCEPT, "apply", "1")
  table.remove(virt_text) -- Remove trailing pipe
  table.insert(virt_text, { " | ", HL_PENDING })
  add_option(STATE_REJECT, "reject", "2")
  table.remove(virt_text) -- Remove last pipe

  -- Icon at the end
  table.insert(virt_text, { " " .. icon .. " ", icon_hl })

  return virt_text
end

-- Update the header line and virtual text
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
  local adds, dels = parse_edit_stats(edit_data.search_content, edit_data.replace_content)

  -- Update header line text
  local stats_line = string.format("+%d -%d | %s", adds, dels, edit_data.filename)

  local current_line = vim.api.nvim_buf_get_lines(bufnr, row, row + 1, false)[1]
  if current_line ~= stats_line then
    vim.api.nvim_buf_set_lines(bufnr, row, row + 1, false, { stats_line })
  end

  -- Update virtual text
  local virt_text = get_header_virt_text(edit_data.state, adds, dels)

  local height = edit_data.end_row - edit_data.header_row
  local current_end_row = row + height

  vim.api.nvim_buf_set_extmark(bufnr, ns_id, row, 0, {
    id = extmark_id,
    virt_text = virt_text,
    virt_text_pos = "right_align",
    end_row = current_end_row,
  })

  -- Apply highlights to header line
  local line = stats_line
  local s_add, e_add = line:find("%+%d+")
  if s_add then
    vim.api.nvim_buf_add_highlight(bufnr, ns_id, "OkMsg", row, s_add - 1, e_add)
  end

  local s_del, e_del = line:find("%-%d+")
  if s_del then
    vim.api.nvim_buf_add_highlight(bufnr, ns_id, "ErrorMsg", row, s_del - 1, e_del)
  end

  -- Highlight filename
  local s_file = line:find("| (.+)")
  if s_file then
    vim.api.nvim_buf_add_highlight(bufnr, ns_id, HL_FILENAME, row, s_file + 1, -1)
  end
end

-- Handle keypress for apply/reject
function M.handle_keypress(bufnr, key)
  local cursor = vim.api.nvim_win_get_cursor(0)
  local row = cursor[1] - 1

  local extmarks = vim.api.nvim_buf_get_extmarks(bufnr, ns_id, 0, -1, { details = true })

  for _, mark in ipairs(extmarks) do
    local id = mark[1]

    if edit_registry[id] then
      local start_row = mark[2]
      local end_row = mark[4].end_row

      if end_row and row >= start_row and row <= end_row then
        local current_state = edit_registry[id].state
        local new_state
        if key == "1" then
          new_state = STATE_ACCEPT
        elseif key == "2" then
          new_state = STATE_REJECT
        else
          return
        end

        if current_state == new_state then
          return
        end

        edit_registry[id].state = new_state
        update_edit_header(bufnr, id)

        -- Trigger action via vim function
        if new_state == STATE_ACCEPT then
          vim.fn.AgentEditAction("apply", edit_registry[id].raw_block, current_state)
        elseif new_state == STATE_REJECT then
          if current_state == STATE_ACCEPT then
            vim.fn.AgentEditAction("reject", edit_registry[id].raw_block, current_state)
          elseif current_state == STATE_PENDING then
            vim.fn.AgentEditAction("reject_pending", edit_registry[id].raw_block, current_state)
          end
        end
        return
      end
    end
  end
end

-- Render a SEARCH/REPLACE edit block in the buffer
function M.render_edit(bufnr, filename, search_content, replace_content, raw_block)
  local line_count = vim.api.nvim_buf_line_count(bufnr)
  local last_line = vim.api.nvim_buf_get_lines(bufnr, line_count - 1, line_count, false)[1] or ""

  -- Ensure there's exactly one blank line before the edit block
  local start_line
  if last_line == "" then
    local second_last = ""
    if line_count >= 2 then
      second_last = vim.api.nvim_buf_get_lines(bufnr, line_count - 2, line_count - 1, false)[1] or ""
    end
    if second_last == "" then
      start_line = line_count - 1
    else
      start_line = line_count
    end
  else
    vim.api.nvim_buf_set_lines(bufnr, -1, -1, false, { "" })
    start_line = line_count + 1
  end

  -- Calculate stats
  local adds, dels = parse_edit_stats(search_content, replace_content)
  local header_text = string.format("+%d -%d | %s", adds, dels, filename)

  -- Build block lines
  local block_lines = {}
  table.insert(block_lines, header_text)
  table.insert(block_lines, "<<<<<<< SEARCH")

  -- Add search content lines
  local search_lines = vim.split(search_content or "", "\n")
  for _, line in ipairs(search_lines) do
    table.insert(block_lines, line)
  end

  table.insert(block_lines, "=======")

  -- Add replace content lines
  local replace_lines = vim.split(replace_content or "", "\n")
  for _, line in ipairs(replace_lines) do
    table.insert(block_lines, line)
  end

  table.insert(block_lines, ">>>>>>> REPLACE")
  table.insert(block_lines, "")

  vim.api.nvim_buf_set_lines(bufnr, start_line, -1, false, block_lines)

  -- Apply syntax highlighting
  local header_row = start_line
  local current_row = start_line + 1

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

  local end_row = start_line + #block_lines - 1

  -- Create extmark for tracking
  local initial_state = STATE_PENDING
  local virt_text = get_header_virt_text(initial_state, adds, dels)

  local id = vim.api.nvim_buf_set_extmark(bufnr, ns_id, header_row, 0, {
    virt_text = virt_text,
    virt_text_pos = "right_align",
    end_row = end_row,
  })

  edit_registry[id] = {
    state = initial_state,
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

  -- Create fold for the content
  pcall(function()
    require("agent_nvim.folds").create_fold(bufnr, start_line + 1, end_row + 1)
  end)

  M.setup_keymaps(bufnr)
  return id
end

-- Setup keymaps for the buffer
function M.setup_keymaps(bufnr)
  local opts = { noremap = true, silent = true, buffer = bufnr }
  vim.keymap.set("n", "1", function()
    M.handle_keypress(bufnr, "1")
  end, opts)
  vim.keymap.set("n", "2", function()
    M.handle_keypress(bufnr, "2")
  end, opts)
end

-- Get all edits from the buffer
function M.get_edits(bufnr)
  local edits = {}
  local extmarks = vim.api.nvim_buf_get_extmarks(bufnr, ns_id, 0, -1, { details = true })

  for _, mark in ipairs(extmarks) do
    local id = mark[1]
    local data = edit_registry[id]
    if data then
      table.insert(edits, {
        filename = data.filename,
        search_content = data.search_content,
        replace_content = data.replace_content,
        raw_block = data.raw_block,
        state = data.state,
      })
    end
  end
  return edits
end

-- Mark the latest edit as applied (for YOLO mode)
function M.mark_latest_as_applied(bufnr)
  local extmarks = vim.api.nvim_buf_get_extmarks(bufnr, ns_id, 0, -1, { details = true })
  if #extmarks == 0 then
    return false
  end

  local last_id = nil
  for _, mark in ipairs(extmarks) do
    local id = mark[1]
    if edit_registry[id] then
      last_id = id
    end
  end

  if not last_id or not edit_registry[last_id] then
    return false
  end

  edit_registry[last_id].state = STATE_ACCEPT
  update_edit_header(bufnr, last_id)
  return true
end

-- Clear all edit blocks from registry (useful when clearing chat)
function M.clear_registry()
  edit_registry = {}
end

-- Get namespace id for external use
function M.get_namespace()
  return ns_id
end

return M
