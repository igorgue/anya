--[[
Enhanced Tool Call and Output Display for CodeCompanion

This module provides improved display of tool calls and their outputs,
including showing tool parameters, execution status, and better formatting.
--]]

local BaseFormatter = require("codecompanion.strategies.chat.ui.formatters.base")
local config = require("codecompanion.config")
local log = require("codecompanion.utils.log")

local CONSTANTS = {
  icons = {
    pending = config.display.chat.icons.tool_pending or "⏳",
    in_progress = config.display.chat.icons.tool_in_progress or "⚡",
    failed = config.display.chat.icons.tool_failure or "❌",
    completed = config.display.chat.icons.tool_success or "✅",
    -- New icons for tool calls
    tool_call = "🔧",
    parameters = "📋",
    output = "📤",
    duration = "⏱️",
  },
}

---@class CodeCompanion.Chat.UI.Formatters.EnhancedTools : CodeCompanion.Chat.UI.Formatters.Base
local EnhancedTools = setmetatable({}, { __index = BaseFormatter })
EnhancedTools.__class = "EnhancedTools"

function EnhancedTools:can_handle(message, opts, tags)
  return opts and opts.type == tags.TOOL_MESSAGE
end

function EnhancedTools:get_type(opts)
  return self.chat.MESSAGE_TYPES.TOOL_MESSAGE
end

function EnhancedTools:format(message, opts, state)
  local lines = {}
  local content_line_offset = 0

  -- Handle spacing between sections
  if state.has_reasoning_output then
    state:mark_reasoning_complete()
    table.insert(lines, "")
    table.insert(lines, "")
    table.insert(lines, "### Response")
    content_line_offset = 3
  end

  if state.last_type == self.chat.MESSAGE_TYPES.TOOL_MESSAGE then
    table.insert(lines, "")
    content_line_offset = 1
  end

  if state.is_new_block then
    if state.block_index > 0 then
      table.insert(lines, "")
      table.insert(lines, "")
      content_line_offset = content_line_offset + 2
    else
      table.insert(lines, "")
      content_line_offset = content_line_offset + 1
    end
  end

  -- Enhanced display: Show tool call information if available
  if opts.tool_call then
    content_line_offset = content_line_offset + self:_format_tool_call(lines, opts.tool_call, content_line_offset)
  end

  -- Format the main content
  local content = message.content or ""
  
  -- Add status icon if provided
  if opts.status then
    local icon = CONSTANTS.icons[opts.status]
    local content_prefix = ""
    
    if opts.tool_call then
      content_prefix = string.format("%s **Output:** ", CONSTANTS.icons.output)
    else
      content_prefix = icon .. " "
    end
    
    content = content_prefix .. content
    opts._icon_info = {
      status = opts.status,
      has_icon = true,
      line_offset = content_line_offset,
    }
  elseif opts.tool_call then
    -- If we have tool call info but no status, still show as output
    content = string.format("%s **Output:** %s", CONSTANTS.icons.output, content)
  end

  -- Add duration information if available
  if opts.duration then
    content = content .. string.format(" %s %s", CONSTANTS.icons.duration, opts.duration)
  end

  local content_start_index = #lines + 1
  local content_lines = vim.split(content, "\n", { plain = true, trimempty = false })
  for _, line in ipairs(content_lines) do
    table.insert(lines, line)
  end

  -- Handle folding
  if not config.strategies.chat.tools.opts.folds.enabled or opts.status then
    return lines, nil
  end

  local fold_info = nil
  if #content_lines > 1 then
    fold_info = {
      start_offset = content_start_index - 1,
      end_offset = content_start_index + #content_lines - 2,
      first_line = content_lines[1] or "",
    }
  end

  return lines, fold_info
end

-- Format tool call information including name and parameters
function EnhancedTools:_format_tool_call(lines, tool_call, line_offset)
  local added_lines = 0
  
  if not tool_call then
    return added_lines
  end

  local tool_name = tool_call.name or tool_call.function_call and tool_call.function_call.name or "Unknown Tool"
  local tool_id = tool_call.id or tool_call.function_call and tool_call.function_call.id or ""
  
  -- Tool call header
  table.insert(lines, string.format("%s **Tool Call:** `%s`", CONSTANTS.icons.tool_call, tool_name))
  added_lines = added_lines + 1
  
  if tool_id ~= "" then
    table.insert(lines, string.format("   **ID:** `%s`", tool_id))
    added_lines = added_lines + 1
  end
  
  -- Format and display parameters
  local params = self:_extract_parameters(tool_call)
  if params and params ~= "" then
    table.insert(lines, string.format("%s **Parameters:**", CONSTANTS.icons.parameters))
    added_lines = added_lines + 1
    
    -- Format parameters as code block for better readability
    local param_lines = vim.split(params, "\n", { plain = true, trimempty = false })
    table.insert(lines, "```json")
    added_lines = added_lines + 1
    
    for _, line in ipairs(param_lines) do
      table.insert(lines, "   " .. line)
      added_lines = added_lines + 1
    end
    
    table.insert(lines, "```")
    added_lines = added_lines + 1
  end
  
  -- Add separator between tool call and output
  table.insert(lines, "---")
  added_lines = added_lines + 1
  
  return added_lines
end

-- Extract and format parameters from tool call
function EnhancedTools:_extract_parameters(tool_call)
  local args = nil
  
  if tool_call.function_call and tool_call.function_call.arguments then
    args = tool_call.function_call.arguments
  elseif tool_call.args then
    args = tool_call.args
  elseif tool_call.parameters then
    args = tool_call.parameters
  end
  
  if not args then
    return ""
  end
  
  -- If args is a string (JSON), try to format it
  if type(args) == "string" then
    -- Try to parse and reformat JSON for better readability
    local ok, parsed = pcall(vim.json.decode, args)
    if ok then
      return vim.json.encode(parsed)
    else
      return args  -- Return original if parsing fails
    end
  elseif type(args) == "table" then
    return vim.json.encode(args)
  else
    return tostring(args)
  end
end

return EnhancedTools