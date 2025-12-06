-- Conversation management module for Anya plugin
-- Handles creating conversations and sending user messages to the chat buffer

local M = {}
local markers = require("anya.markers")

-- Buffer-local variable names for tracking conversation state
local CONVERSATION_ID_VAR = "anya_conversation_id"

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

--- Get the user's name for message attribution
--- @return string The user's name
local function get_user_name()
  -- Try git config first
  local handle = io.popen("git config user.name 2>/dev/null")
  if handle then
    local result = handle:read("*a")
    handle:close()
    if result and result ~= "" then
      return vim.trim(result)
    end
  end

  -- Fall back to system username
  return os.getenv("USER") or os.getenv("USERNAME") or "User"
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
--- @param text string The message text
--- @return string[] Lines of the formatted message
local function format_user_message(text)
  local lines = vim.split(text, "\n", { plain = true })
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

  -- Add conversation marker if this is a new conversation
  if is_new_conversation then
    table.insert(output_lines, markers.make_conversation_marker(conv_id, timestamp))
  end

  -- Add user header (# Username)
  table.insert(output_lines, "# " .. user_name)

  -- Add message start marker
  table.insert(output_lines, markers.make_user_message_start(msg_id, user_name, timestamp))

  -- Add the message content as blockquote
  local formatted_lines = format_user_message(prompt_text)
  for _, line in ipairs(formatted_lines) do
    table.insert(output_lines, line)
  end

  -- Add message end marker
  table.insert(output_lines, markers.make_message_end(msg_id, timestamp))

  -- Add trailing empty line for spacing
  table.insert(output_lines, "")

  -- Determine where to insert in the chat buffer
  local chat_empty = is_chat_buffer_empty(chat_buf)
  local insert_line

  if chat_empty then
    -- Replace the empty buffer content
    insert_line = 0
  else
    -- Append after existing content
    insert_line = vim.api.nvim_buf_line_count(chat_buf)
    -- Add a blank line separator if the last line isn't empty
    local last_line = vim.api.nvim_buf_get_lines(chat_buf, -2, -1, false)[1] or ""
    if last_line ~= "" then
      table.insert(output_lines, 1, "")
    end
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

  -- Process markers to create folds and extmarks
  local text = require("anya.text")
  text._process_markers(chat_buf)

  -- Scroll chat buffer to bottom
  text._autoscroll_to_bottom(chat_buf)

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

return M
