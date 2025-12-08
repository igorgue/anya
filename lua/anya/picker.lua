-- Conversation picker using Snacks.picker
-- Shows recent conversations with preview and allows loading them

local M = {}

local markers = require("anya.markers")

--- Strip marker lines from content for preview display
--- @param content string The content with markers
--- @return string Content without marker lines
local function strip_markers(content)
  local lines = vim.split(content, "\n", { plain = true })
  local result = {}

  for _, line in ipairs(lines) do
    -- Skip marker lines
    if
      not markers.is_marker_line(line)
      and not markers.is_message_marker(line)
      and not markers.is_conversation_marker(line)
    then
      table.insert(result, line)
    end
  end

  return table.concat(result, "\n")
end

--- Format a timestamp for display
--- @param timestamp string ISO 8601 timestamp
--- @return string Formatted date string
local function format_timestamp(timestamp)
  if not timestamp then
    return ""
  end

  -- Parse ISO 8601 format: YYYY-MM-DDTHH:MM:SSZ
  local year, month, day, hour, min = timestamp:match("(%d+)-(%d+)-(%d+)T(%d+):(%d+)")
  if not year then
    return timestamp
  end

  -- Format as "Dec 06, 2:30pm"
  local months = { "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec" }
  local month_name = months[tonumber(month)] or month

  local hour_num = tonumber(hour) or 0
  local hour_12 = hour_num % 12
  if hour_12 == 0 then
    hour_12 = 12
  end
  local ampm = hour_num >= 12 and "pm" or "am"

  return string.format("%s %d, %d:%02d%s", month_name, tonumber(day), hour_12, tonumber(min), ampm)
end

--- Get the chat buffer by looking for a buffer with filetype "anya-chat"
--- @return number|nil Buffer number or nil if not found
local function get_chat_buffer()
  for _, buf in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_is_valid(buf) then
      local ft = vim.api.nvim_get_option_value("filetype", { buf = buf })
      if ft == "anya-chat" then
        return buf
      end
    end
  end
  return nil
end

--- Load a conversation into the chat buffer
--- @param conversation_id string The conversation ID to load
--- @return boolean True if successful
local function load_conversation(conversation_id)
  local chat_buf = get_chat_buffer()
  if not chat_buf then
    vim.notify("Anya: Chat buffer not found. Run :Anya to open the interface.", vim.log.levels.ERROR)
    return false
  end

  -- Get the rebuilt buffer content from Python
  local content = vim.fn.AnyaRebuildBufferContent(conversation_id)
  if not content then
    vim.notify("Anya: Failed to load conversation.", vim.log.levels.ERROR)
    return false
  end

  -- Set the buffer content directly (no animation)
  local was_modifiable = vim.api.nvim_get_option_value("modifiable", { buf = chat_buf })
  vim.api.nvim_set_option_value("modifiable", true, { buf = chat_buf })

  -- Replace all buffer content
  local lines = vim.split(content, "\n", { plain = true })
  vim.api.nvim_buf_set_lines(chat_buf, 0, -1, false, lines)

  vim.api.nvim_set_option_value("modifiable", was_modifiable, { buf = chat_buf })

  -- Set the conversation ID on the buffer
  vim.api.nvim_buf_set_var(chat_buf, "anya_conversation_id", conversation_id)

  -- Process markers to create folds and extmarks
  local text = require("anya.text")
  text._process_markers(chat_buf)

  -- Setup keymaps for edit widgets (if any)
  local edit_view = require("anya.edit_view")
  edit_view.rebuild_registry(chat_buf)
  edit_view.setup_keymaps(chat_buf)

  -- Scroll to bottom
  text._autoscroll_to_bottom(chat_buf)

  return true
end

--- Build preview lines for a conversation
--- @param conversation_id string The conversation ID
--- @return string[] Preview lines
local function get_preview_lines(conversation_id)
  local content = vim.fn.AnyaRebuildBufferContent(conversation_id)
  if not content then
    return { "Failed to load conversation preview." }
  end

  -- Strip markers for cleaner preview
  local clean_content = strip_markers(content)
  return vim.split(clean_content, "\n", { plain = true })
end

--- Open the conversation picker
function M.open()
  -- Check if Snacks is available
  local ok, Snacks = pcall(require, "snacks")
  if not ok then
    vim.notify("Anya: snacks.nvim is required for the conversation picker.", vim.log.levels.ERROR)
    return
  end

  -- Get conversations from Python
  local conversations = vim.fn.AnyaListConversations(20, 0)
  if not conversations or #conversations == 0 then
    vim.notify("Anya: No conversations found.", vim.log.levels.INFO)
    return
  end

  -- Build items for the picker
  local items = {}
  for i, conv in ipairs(conversations) do
    local title = conv.title or "Untitled conversation"
    local date = format_timestamp(conv.updated_at)

    table.insert(items, {
      idx = i,
      text = title,
      id = conv.id,
      title = title,
      date = date,
      updated_at = conv.updated_at,
      created_at = conv.created_at,
    })
  end

  -- Create the picker
  Snacks.picker.pick({
    title = "Conversations",
    items = items,
    format = function(item, _ctx)
      local ret = {}
      -- Title
      table.insert(ret, { item.title, "SnacksPickerLabel" })
      -- Date (right-aligned effect with padding)
      table.insert(ret, { "  " })
      table.insert(ret, { item.date, "SnacksPickerComment" })
      return ret
    end,
    preview = function(ctx)
      local item = ctx.item
      if not item or not item.id then
        return
      end

      local lines = get_preview_lines(item.id)

      -- Make buffer modifiable, set content, then restore
      vim.api.nvim_set_option_value("modifiable", true, { buf = ctx.buf })
      vim.api.nvim_buf_set_lines(ctx.buf, 0, -1, false, lines)
      vim.api.nvim_set_option_value("modifiable", false, { buf = ctx.buf })

      -- Set filetype for markdown highlighting
      vim.api.nvim_set_option_value("filetype", "markdown", { buf = ctx.buf })
    end,
    confirm = function(picker, item)
      picker:close()
      if item and item.id then
        load_conversation(item.id)
      end
    end,
    layout = {
      preset = "default",
      preview = true,
    },
  })
end

return M
