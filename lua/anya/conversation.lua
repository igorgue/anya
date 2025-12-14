-- Conversation management module for Anya plugin
-- Handles creating conversations and sending user messages to the chat buffer

local M = {}
local markers = require("anya.markers")
local text = require("anya.text")

-- Buffer-local variable names for tracking conversation state
local CONVERSATION_ID_VAR = "anya_conversation_id"

-- Track whether a request is currently in progress (Python agent running)
M._request_in_progress = false

--- Check if streaming is still in progress (either agent running or queue not empty)
--- @return boolean True if streaming is in progress
local function is_streaming_in_progress()
  -- Check if Python agent is still running
  if M._request_in_progress then
    return true
  end
  -- Check if Lua streaming queue still has content
  local status = text.get_queue_status()
  return status.queue_length > 0 or status.timer_running
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

--- Get the prompt buffer by looking for a buffer with filetype "anya-prompt"
--- @return number|nil Buffer number or nil if not found
local function get_prompt_buffer()
  for _, buf in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_is_valid(buf) then
      local ft = vim.api.nvim_get_option_value("filetype", { buf = buf })
      if ft == "anya-prompt" then
        return buf
      end
    end
  end
  return nil
end

--- Get the current conversation ID from the chat buffer, or nil if no conversation exists
--- @param chat_buf number The chat buffer number
--- @return string|nil The conversation ID or nil
local function get_conversation_id(chat_buf)
  local ok, conv_id = pcall(vim.api.nvim_buf_get_var, chat_buf, CONVERSATION_ID_VAR)
  if ok and conv_id and conv_id ~= "" then
    return conv_id
  end
  return nil
end

--- Set the conversation ID on the chat buffer
--- @param chat_buf number The chat buffer number
--- @param conv_id string The conversation ID
local function set_conversation_id(chat_buf, conv_id)
  vim.api.nvim_buf_set_var(chat_buf, CONVERSATION_ID_VAR, conv_id)
end

--- Titlecase a string (capitalize first letter of each word)
--- @param str string The string to titlecase
--- @return string The titlecased string
local function titlecase(str)
  return str:gsub("(%a)([%w]*)", function(first, rest)
    return first:upper() .. rest:lower()
  end)
end

--- Get the user's name for message attribution
--- @return string The user's name (titlecased)
local function get_user_name()
  -- Fall back to system username
  local name = os.getenv("USERNAME") or os.getenv("USER") or "User"
  return titlecase(name)
end

--- Check if the chat buffer is empty or only contains whitespace
--- @param chat_buf number The chat buffer number
--- @return boolean True if the buffer is empty
local function is_chat_buffer_empty(chat_buf)
  local lines = vim.api.nvim_buf_get_lines(chat_buf, 0, -1, false)
  for _, line in ipairs(lines) do
    if line:match("%S") then
      return false
    end
  end
  return true
end

--- Format the user message content as a blockquote
--- @param msg_text string The message text
--- @return string[] Lines of the formatted message
local function format_user_message(msg_text)
  local lines = vim.split(msg_text, "\n", { plain = true })
  local result = {}
  for _, line in ipairs(lines) do
    table.insert(result, "> " .. line)
  end
  return result
end

--- Send the current prompt buffer content as a user message to the chat buffer
--- Creates a new conversation if one doesn't exist
--- @return boolean True if the message was sent successfully
function M.send_message()
  -- Block sending while streaming is in progress (agent running or queue not empty)
  if is_streaming_in_progress() then
    vim.notify("Anya: Please wait for the current response to complete.", vim.log.levels.WARN)
    return false
  end

  local chat_buf = get_chat_buffer()
  local prompt_buf = get_prompt_buffer()

  if not chat_buf then
    vim.notify("Anya: Chat buffer not found. Run :Anya to open the interface.", vim.log.levels.ERROR)
    return false
  end

  if not prompt_buf then
    vim.notify("Anya: Prompt buffer not found. Run :Anya to open the interface.", vim.log.levels.ERROR)
    return false
  end

  -- Get the prompt text
  local prompt_lines = vim.api.nvim_buf_get_lines(prompt_buf, 0, -1, false)
  local prompt_text = table.concat(prompt_lines, "\n")

  -- Don't send empty messages
  if not prompt_text:match("%S") then
    return false
  end

  -- Get or create conversation ID
  local conv_id = get_conversation_id(chat_buf)
  local is_new_conversation = conv_id == nil

  if is_new_conversation then
    -- Generate new conversation ID via Python
    conv_id = vim.fn.AnyaNewConversationId()
    set_conversation_id(chat_buf, conv_id)
  end

  -- Generate message ID and timestamp
  local msg_id = vim.fn.AnyaNewMessageId(conv_id)
  local timestamp = vim.fn.AnyaTimestamp()
  local user_name = get_user_name()

  -- Build the message content
  local output_lines = {}

  -- Add message marker
  table.insert(output_lines, markers.make_message_marker(msg_id))

  -- Add the message content as blockquote
  local formatted_lines = format_user_message(prompt_text)
  for _, line in ipairs(formatted_lines) do
    table.insert(output_lines, line)
  end

  -- Add trailing empty line for spacing
  table.insert(output_lines, "")

  -- Determine where to insert in the chat buffer
  local chat_empty = is_chat_buffer_empty(chat_buf)
  local insert_line

  if chat_empty then
    -- Replace the empty buffer content
    insert_line = 0
  else
    -- Remove trailing blank lines before adding new message
    local was_modifiable = vim.api.nvim_get_option_value("modifiable", { buf = chat_buf })
    vim.api.nvim_set_option_value("modifiable", true, { buf = chat_buf })
    local line_count = vim.api.nvim_buf_line_count(chat_buf)
    while line_count > 0 do
      local last_line = vim.api.nvim_buf_get_lines(chat_buf, line_count - 1, line_count, false)[1] or ""
      if last_line == "" then
        vim.api.nvim_buf_set_lines(chat_buf, line_count - 1, line_count, false, {})
        line_count = line_count - 1
      else
        break
      end
    end
    vim.api.nvim_set_option_value("modifiable", was_modifiable, { buf = chat_buf })
    -- Append after existing content
    insert_line = vim.api.nvim_buf_line_count(chat_buf)
  end

  -- Make buffer modifiable, insert lines, then restore
  local was_modifiable = vim.api.nvim_get_option_value("modifiable", { buf = chat_buf })
  vim.api.nvim_set_option_value("modifiable", true, { buf = chat_buf })

  if chat_empty then
    vim.api.nvim_buf_set_lines(chat_buf, 0, -1, false, output_lines)
  else
    vim.api.nvim_buf_set_lines(chat_buf, insert_line, insert_line, false, output_lines)
  end

  vim.api.nvim_set_option_value("modifiable", was_modifiable, { buf = chat_buf })

  -- Clear the prompt buffer
  vim.api.nvim_buf_set_lines(prompt_buf, 0, -1, false, { "" })

  -- Save to database before rendering extmarks
  if is_new_conversation then
    vim.fn.AnyaSaveConversation(conv_id, timestamp)
  end
  vim.fn.AnyaSaveMessage(msg_id, conv_id, "user", prompt_text, user_name, nil, timestamp, timestamp, nil)

  -- Process markers to create folds and extmarks
  text._process_markers(chat_buf)

  -- Scroll chat buffer to bottom
  text._autoscroll_to_bottom(chat_buf)

  -- Send to agent for response (async)
  vim.fn.AnyaSend(prompt_text, conv_id)

  return true
end

--- Clear the current conversation and start fresh
function M.clear_conversation()
  local chat_buf = get_chat_buffer()
  if not chat_buf then
    return
  end

  -- Clear the conversation ID
  pcall(vim.api.nvim_buf_del_var, chat_buf, CONVERSATION_ID_VAR)

  -- Clear the buffer content
  local was_modifiable = vim.api.nvim_get_option_value("modifiable", { buf = chat_buf })
  vim.api.nvim_set_option_value("modifiable", true, { buf = chat_buf })
  vim.api.nvim_buf_set_lines(chat_buf, 0, -1, false, { "" })
  vim.api.nvim_set_option_value("modifiable", was_modifiable, { buf = chat_buf })
end

--- Get the current conversation ID (for external use)
--- @return string|nil The conversation ID or nil
function M.get_current_conversation_id()
  local chat_buf = get_chat_buffer()
  if not chat_buf then
    return nil
  end
  return get_conversation_id(chat_buf)
end

--- Get the current message ID by searching backward from cursor for message start marker
--- @param bufnr number|nil Buffer number (defaults to chat buffer)
--- @param from_line number|nil Line number to search backward from (defaults to cursor line, 1-indexed)
--- @return string|nil The message ID or nil
function M.get_current_message_id(bufnr, from_line)
  bufnr = bufnr or get_chat_buffer()
  if not bufnr or not vim.api.nvim_buf_is_valid(bufnr) then
    return nil
  end

  local cursor_line = from_line or 1
  if not from_line then
    local ok, cursor = pcall(vim.api.nvim_win_get_cursor, 0)
    if ok then
      cursor_line = cursor[1]
    end
  end

  -- Get lines from 0 (0-indexed) to cursor_line (converts to 0-indexed)
  -- vim.api.nvim_buf_get_lines uses 0-indexed, so we need end=cursor_line to include cursor_line
  local lines = vim.api.nvim_buf_get_lines(bufnr, 0, cursor_line, false)

  for i = #lines, 1, -1 do
    local line = lines[i]
    local msg_id = line:match("<!%-%- am: ([^%s]+) %-%->")
    if msg_id then
      return msg_id
    end
  end

  return nil
end

--- Check if streaming is currently in progress (agent running or queue not empty)
--- @return boolean True if streaming is in progress
function M.is_request_in_progress()
  return is_streaming_in_progress()
end

--- Force reset request state (for cancellation)
function M.force_reset_request_state()
  M._request_in_progress = false
end

--- Set up autocommands to track request state
--- Called once during plugin initialization
function M.setup_request_tracking()
  local group = vim.api.nvim_create_augroup("AnyaRequestTracking", { clear = true })

  vim.api.nvim_create_autocmd("User", {
    pattern = "AnyaRequestStarted",
    group = group,
    callback = function()
      M._request_in_progress = true
    end,
    desc = "Track when Anya request starts",
  })

  vim.api.nvim_create_autocmd("User", {
    pattern = "AnyaRequestFinished",
    group = group,
    callback = function()
      M._request_in_progress = false
      -- Allow user to continue even if request was cancelled
      -- This enables sending follow-up messages
    end,
    desc = "Track when Anya request finishes",
  })
end

-- Initialize request tracking when module loads
M.setup_request_tracking()

--- Toggle YOLO mode on/off and show notification
--- @return boolean The new YOLO mode state
function M.toggle_yolo_mode()
  local new_state = vim.fn.AnyaToggleYoloMode()
  local status_text = new_state and "ON" or "OFF"
  local level = new_state and vim.log.levels.WARN or vim.log.levels.INFO

  -- Refresh winbar to show updated YOLO state
  -- Find the chat window and force winbar expression to re-evaluate
  vim.schedule(function()
    -- Find chat window
    local chat_win = nil
    for _, win in ipairs(vim.api.nvim_list_wins()) do
      if vim.api.nvim_win_is_valid(win) then
        local buf = vim.api.nvim_win_get_buf(win)
        local ft = vim.api.nvim_buf_get_option(buf, "filetype")
        if ft == "anya-chat" then
          chat_win = win
          break
        end
      end
    end

    if chat_win then
      -- Force winbar to re-evaluate by resetting it
      local current_winbar = vim.api.nvim_win_get_option(chat_win, "winbar")
      if current_winbar then
        -- Reset the winbar to force expression re-evaluation
        vim.api.nvim_win_set_option(chat_win, "winbar", "")
        vim.api.nvim_win_set_option(chat_win, "winbar", current_winbar)
      end
      -- Also force redraw
      vim.cmd("redrawstatus")
    else
      -- Fallback: just redraw if we can't find the window
      vim.cmd("redrawstatus")
    end
  end)

  return new_state
end

return M
