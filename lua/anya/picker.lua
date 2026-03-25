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
    if not markers.is_marker_line(line) and not markers.is_message_marker(line) then
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

--- Shorten a path for display
--- @param path string The full path
--- @return string Shortened path
local function shorten_path(path)
  if not path or path == vim.NIL or path == "" then
    return ""
  end
  if type(path) ~= "string" then
    path = tostring(path)
  end
  -- Replace home directory with ~
  local home = vim.loop.os_getenv("HOME") or ""
  if home ~= "" and path:sub(1, #home) == home then
    path = "~" .. path:sub(#home + 1)
  end
  return path
end

--- Normalize msgpack nil/userdata values into Lua strings
--- @param value any
--- @param default string|nil
--- @return string|nil
local function as_string(value, default)
  if value == nil or value == vim.NIL then
    return default
  end
  if type(value) ~= "string" then
    value = tostring(value)
  end
  if value == "" then
    return default
  end
  return value
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

--- Get a visible chat window for the given buffer.
--- @param chat_buf number
--- @return number|nil
local function get_chat_window(chat_buf)
  for _, win in ipairs(vim.api.nvim_list_wins()) do
    if vim.api.nvim_win_is_valid(win) and vim.api.nvim_win_get_buf(win) == chat_buf then
      return win
    end
  end
  return nil
end

--- Load a conversation into the chat buffer
--- @param conversation_id string The conversation ID to load
--- @param new_cwd string|nil Optional new cwd to switch to
--- @param title string|nil Optional conversation title to set as window title
--- @return boolean True if successful
local function load_conversation(conversation_id, new_cwd, title)
  local chat_buf = get_chat_buffer()
  if not chat_buf then
    vim.notify("Anya: Chat buffer not found. Run :Anya to open the interface.", vim.log.levels.ERROR)
    return false
  end

  -- Switch cwd if requested
  if new_cwd and new_cwd ~= "" then
    vim.cmd("cd " .. vim.fn.fnameescape(new_cwd))
    vim.notify("Anya: Switched to " .. shorten_path(new_cwd), vim.log.levels.INFO)
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

  -- Update window title if the conversation has a title
  title = as_string(title)
  if title then
    vim.o.titlestring = "Anya: " .. title
  end

  -- Process markers to create folds and extmarks
  local text = require("anya.text")
  text._process_markers(chat_buf)
  if _G.anya_highlight_chat_file_refs then
    _G.anya_highlight_chat_file_refs()
  end

  local chat_win = get_chat_window(chat_buf)
  if chat_win and vim.api.nvim_win_is_valid(chat_win) then
    vim.api.nvim_set_current_win(chat_win)
    local last_line = math.max(vim.api.nvim_buf_line_count(chat_buf), 1)
    vim.api.nvim_win_set_cursor(chat_win, { last_line, 0 })
    text._force_autoscroll_to_bottom(chat_buf)
  else
    text._force_autoscroll_to_bottom(chat_buf)
  end

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

--- Show a confirmation dialog for cwd mismatch
--- @param item table The conversation item
--- @param callback function Called with "switch", "load", or "cancel"
local function show_cwd_confirmation(item, callback)
  local conv_path = shorten_path(item.cwd)
  local current_path = shorten_path(vim.loop.cwd() or "")

  -- Use vim.ui.select for the choice
  local prompt = "Different project: " .. conv_path .. "  (current: " .. current_path .. ")"
  vim.ui.select({ "Switch to project and load", "Load anyway", "Cancel" }, {
    prompt = prompt,
    format_item = function(choice)
      return choice
    end,
  }, function(choice, idx)
    if not choice or idx == 3 then
      callback("cancel")
    elseif idx == 1 then
      callback("switch")
    elseif idx == 2 then
      callback("load")
    end
  end)
end

--- Open the conversation picker
function M.open()
  -- Check if Snacks is available
  local ok, Snacks = pcall(require, "snacks")
  if not ok then
    vim.notify("Anya: snacks.nvim is required for the conversation picker.", vim.log.levels.ERROR)
    return
  end

  -- Get current cwd for comparison
  local current_cwd = vim.loop.cwd() or ""

  -- Get all conversations from Python so the picker can search the full history
  local conversation_count = vim.fn.AnyaCountConversations()
  if not conversation_count or conversation_count == 0 then
    vim.notify("Anya: No conversations found.", vim.log.levels.INFO)
    return
  end

  local conversations = vim.fn.AnyaListConversations(conversation_count, 0)

  -- Build items for the picker
  local items = {}
  for i, conv in ipairs(conversations) do
    local title = as_string(conv.title, "Untitled conversation")
    local date = format_timestamp(as_string(conv.updated_at, ""))
    local conv_cwd = as_string(conv.cwd, "")
    local cwd_matches = conv_cwd == "" or conv_cwd == current_cwd

    table.insert(items, {
      idx = i,
      text = table.concat({
        title,
        conv.id or "",
        conv_cwd,
        as_string(conv.updated_at, ""),
        as_string(conv.created_at, ""),
      }, " "),
      id = conv.id,
      title = title,
      date = date,
      updated_at = conv.updated_at,
      created_at = conv.created_at,
      cwd = conv_cwd,
      cwd_matches = cwd_matches,
    })
  end

  -- Create the picker
  Snacks.picker.pick({
    title = "Conversations",
    items = items,
    format = function(item, _ctx)
      local ret = {}
      -- Warning indicator for different cwd
      if not item.cwd_matches then
        table.insert(ret, { "⚠ ", "DiagnosticWarn" })
      else
        table.insert(ret, { "  " })
      end
      -- Title
      table.insert(ret, { item.title or "Untitled conversation", "SnacksPickerLabel" })
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

      -- Add warning header if cwd differs
      if not item.cwd_matches then
        local warning_lines = {
          "⚠ WARNING: Different working directory",
          "",
          "This conversation was created in:",
          "  " .. shorten_path(item.cwd),
          "",
          "Current directory:",
          "  " .. shorten_path(current_cwd),
          "",
          "Opening files from this conversation may fail or edit the wrong files.",
          "Press Enter for options.",
          "",
          "────────────────────────────────────────",
          "",
        }
        lines = vim.list_extend(warning_lines, lines)
      end

      -- Make buffer modifiable, set content, then restore
      vim.api.nvim_set_option_value("modifiable", true, { buf = ctx.buf })
      vim.api.nvim_buf_set_lines(ctx.buf, 0, -1, false, lines)
      vim.api.nvim_set_option_value("modifiable", false, { buf = ctx.buf })

      -- Set filetype for markdown highlighting
      vim.api.nvim_set_option_value("filetype", "markdown", { buf = ctx.buf })
    end,
    confirm = function(picker, item)
      picker:close()
      if not item or not item.id then
        return
      end

      -- If cwd matches or no cwd stored, load directly
      if item.cwd_matches then
        load_conversation(item.id, nil, item.title)
        return
      end

      -- Show confirmation dialog for cwd mismatch
      show_cwd_confirmation(item, function(action)
        if action == "switch" then
          load_conversation(item.id, item.cwd, item.title)
        elseif action == "load" then
          load_conversation(item.id, nil, item.title)
        end
        -- "cancel" does nothing
      end)
    end,
    layout = {
      preset = "default",
      preview = true,
    },
  })
end

return M
