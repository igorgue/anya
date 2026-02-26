-- Conversation management module for Anya plugin
-- Handles creating conversations and sending user messages to the chat buffer

local M = {}
local markers = require("anya.markers")
local text = require("anya.text")

-- Buffer-local variable names for tracking conversation state
local CONVERSATION_ID_VAR = "anya_conversation_id"

-- Track whether a request is currently in progress
-- This is set IMMEDIATELY when user initiates send, before any RPC
M._sending_in_progress = false

-- Track whether Python agent is running (set via autocommand)
M._request_in_progress = false

-- Timestamp when _request_in_progress was last set to true (for timeout detection)
M._request_started_at = nil

-- Maximum time (seconds) to consider a request in-progress before assuming it's stuck
local REQUEST_TIMEOUT_SECONDS = 300

--- Check if we should block sending
--- @return boolean True if sending should be blocked
local function is_send_blocked()
  -- Block if we're already in the process of sending
  if M._sending_in_progress then
    return true
  end
  -- Block if Python agent is still running (with timeout safety)
  if M._request_in_progress then
    if M._request_started_at then
      local elapsed = vim.loop.now() - M._request_started_at
      if elapsed > REQUEST_TIMEOUT_SECONDS * 1000 then
        -- Request timed out, force reset
        M._request_in_progress = false
        M._request_started_at = nil
        return false
      end
    end
    return true
  end
  -- Block if Lua streaming queue still has content AND timer is running
  -- If timer is not running but queue has items, the queue is stale/stuck — don't block
  local status = text.get_queue_status()
  if status.queue_length > 0 and status.timer_running then
    return true
  end
  -- Clear stale queue items (timer died but items remain)
  if status.queue_length > 0 and not status.timer_running then
    text.clear_queue()
  end
  return false
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
  -- Block immediately if another send is in progress
  if is_send_blocked() then
    vim.notify("Anya: Please wait for the current response to complete.", vim.log.levels.WARN)
    return false
  end

  -- Set the lock IMMEDIATELY before any RPC calls
  M._sending_in_progress = true

  local chat_buf = get_chat_buffer()
  local prompt_buf = get_prompt_buffer()

  if not chat_buf then
    M._sending_in_progress = false
    vim.notify("Anya: Chat buffer not found. Run :Anya to open the interface.", vim.log.levels.ERROR)
    return false
  end

  if not prompt_buf then
    M._sending_in_progress = false
    vim.notify("Anya: Prompt buffer not found. Run :Anya to open the interface.", vim.log.levels.ERROR)
    return false
  end

  -- Get the prompt text
  local prompt_lines = vim.api.nvim_buf_get_lines(prompt_buf, 0, -1, false)
  local prompt_text = table.concat(prompt_lines, "\n")

  -- Don't send empty messages
  if not prompt_text:match("%S") then
    M._sending_in_progress = false
    return false
  end

  -- Get existing conversation ID (if any)
  local existing_conv_id = get_conversation_id(chat_buf)

  -- Clear the prompt buffer immediately for responsiveness
  -- Ensure buffer is modifiable (might be set to non-modifiable by history navigation)
  vim.api.nvim_set_option_value("modifiable", true, { buf = prompt_buf })
  vim.api.nvim_buf_set_lines(prompt_buf, 0, -1, false, { "" })

  -- Single RPC call: send text, get back IDs, schedules agent task
  -- Server handles: ID generation, timestamp, database save
  local ok, result = pcall(vim.fn.AnyaSend, prompt_text, existing_conv_id)
  if not ok then
    M._sending_in_progress = false
    vim.notify("Anya: Failed to send message: " .. tostring(result), vim.log.levels.ERROR)
    return false
  end

  -- Slash commands return nil
  if result == nil or result == vim.NIL then
    M._sending_in_progress = false
    return true
  end

  -- Extract IDs from server response
  local conv_id = result.conv_id
  local msg_id = result.msg_id

  -- Store conversation ID if new
  if result.is_new then
    set_conversation_id(chat_buf, conv_id)
  end

  -- Determine where to insert in the chat buffer
  local chat_empty = is_chat_buffer_empty(chat_buf)

  -- Build the message content
  local output_lines = {}

  -- Add message marker
  table.insert(output_lines, markers.make_message_marker(msg_id))

  -- Add the message content as blockquote
  local formatted_lines = format_user_message(prompt_text)
  for _, line in ipairs(formatted_lines) do
    table.insert(output_lines, line)
  end

  -- Ensure marker isolation
  local final_text = table.concat(output_lines, "\n")
  final_text = markers.ensure_marker_line_isolation(final_text)

  output_lines = vim.split(final_text, "\n", { plain = true })

  local insert_line

  if chat_empty then
    -- Replace the empty buffer content
    insert_line = 0
  else
    -- Ensure NO blank lines before the new message marker
    local was_modifiable = vim.api.nvim_get_option_value("modifiable", { buf = chat_buf })
    vim.api.nvim_set_option_value("modifiable", true, { buf = chat_buf })

    local line_count = vim.api.nvim_buf_line_count(chat_buf)
    -- Remove all trailing blank or whitespace-only lines
    while line_count > 0 do
      local last_line = vim.api.nvim_buf_get_lines(chat_buf, line_count - 1, line_count, false)[1] or ""
      if last_line:match("^%s*$") then
        vim.api.nvim_buf_set_lines(chat_buf, line_count - 1, line_count, false, {})
        line_count = line_count - 1
      else
        -- Strip trailing whitespace from the last line with content
        local stripped = last_line:gsub("%s+$", "")
        if stripped ~= last_line then
          vim.api.nvim_buf_set_lines(chat_buf, line_count - 1, line_count, false, { stripped })
        end
        break
      end
    end

    -- Just append on the next line
    insert_line = line_count

    vim.api.nvim_set_option_value("modifiable", was_modifiable, { buf = chat_buf })
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

  -- Process markers to create folds and extmarks
  text._process_markers(chat_buf)

  -- Scroll chat buffer to bottom
  text._autoscroll_to_bottom(chat_buf)

  -- Save prompt to history
  local history = require("anya.history")
  history.add(prompt_text)

  -- Handoff: clear sending lock, set request lock
  M._sending_in_progress = false
  M._request_in_progress = true
  M._request_started_at = vim.loop.now()

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
  return is_send_blocked()
end

--- Force reset request state (for cancellation)
function M.force_reset_request_state()
  M._sending_in_progress = false
  M._request_in_progress = false
  M._request_started_at = nil
end

--- Set up autocommands to track request state
--- Called once during plugin initialization
function M.setup_request_tracking()
  local group = vim.api.nvim_create_augroup("AnyaRequestTracking", { clear = true })

  vim.api.nvim_create_autocmd("User", {
    pattern = "AnyaRequestStarted",
    group = group,
    callback = function()
      -- Handoff: clear the send lock, agent is now running
      M._sending_in_progress = false
      M._request_in_progress = true
      M._request_started_at = vim.loop.now()
    end,
    desc = "Track when Anya request starts",
  })

  vim.api.nvim_create_autocmd("User", {
    pattern = "AnyaRequestFinished",
    group = group,
    callback = function()
      M._sending_in_progress = false
      M._request_in_progress = false
      M._request_started_at = nil
    end,
    desc = "Track when Anya request finishes",
  })
end

-- Initialize request tracking when module loads
M.setup_request_tracking()


return M
