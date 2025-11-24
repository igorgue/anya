--[[
Tool Monitor for Enhanced Tool Call Display

This module provides real-time monitoring and display of tool execution,
including tool calls, status updates, and outputs with enhanced formatting.
--]]

local config = require("codecompanion.config")
local log = require("codecompanion.utils.log")
local ui_utils = require("codecompanion.utils.ui")

local M = {}

---@class CodeCompanion.ToolMonitor
---@field chat CodeCompanion.Chat
---@field active_tools table<string, table>  -- tool_id -> tool_info
---@field start_times table<string, number>  -- tool_id -> start_time
---@field bufnr number
local ToolMonitor = {}

-- Status icons for different tool states
local STATUS_ICONS = {
  pending = "⏳",
  in_progress = "⚡",
  failed = "❌", 
  completed = "✅",
}

---@param chat CodeCompanion.Chat
---@return CodeCompanion.ToolMonitor
function ToolMonitor.new(chat)
  return setmetatable({
    chat = chat,
    active_tools = {},
    start_times = {},
    bufnr = chat.bufnr,
  }, { __index = ToolMonitor })
end

-- Start monitoring a tool call
---@param tool_call table The tool call object
function ToolMonitor:start_tool(tool_call)
  local tool_id = tool_call.id or tool_call.function_call and tool_call.function_call.id
  if not tool_id then
    log:warn("Tool call missing ID, cannot monitor")
    return
  end
  
  local tool_name = tool_call.name or tool_call.function_call and tool_call.function_call.name or "Unknown Tool"
  
  -- Store tool information
  self.active_tools[tool_id] = {
    tool_call = tool_call,
    name = tool_name,
    status = "in_progress",
    start_time = vim.loop.hrtime(),
  }
  self.start_times[tool_id] = vim.loop.hrtime()
  
  log:debug("Started monitoring tool: %s (ID: %s)", tool_name, tool_id)
  
  -- Show initial tool call with pending status
  self:_show_tool_call(tool_call, "pending")
end

-- Update tool status
---@param tool_id string The tool identifier
---@param status string New status ("in_progress", "completed", "failed")
---@param output string? Optional output to display
function ToolMonitor:update_status(tool_id, status, output)
  local tool_info = self.active_tools[tool_id]
  if not tool_info then
    log:warn("Tool ID %s not found in active tools", tool_id)
    return
  end
  
  tool_info.status = status
  
  if status == "completed" or status == "failed" then
    local duration = self:_calculate_duration(tool_id)
    tool_info.duration = duration
    
    log:debug("Tool %s %s in %s", tool_info.name, status, duration)
    
    -- Show final status with output and duration
    self:_show_tool_output(tool_info.tool_call, status, output, duration)
    
    -- Clean up
    self.active_tools[tool_id] = nil
    self.start_times[tool_id] = nil
  else
    -- Show status update
    self:_show_status_update(tool_info.tool_call, status)
  end
end

-- Display tool call information
---@param tool_call table The tool call object
---@param status string Initial status
function ToolMonitor:_show_tool_call(tool_call, status)
  local tool_name = tool_call.name or tool_call.function_call and tool_call.function_call.name or "Unknown Tool"
  local tool_id = tool_call.id or tool_call.function_call and tool_call.function_call.id or ""
  
  -- Create a formatted tool call display
  local content = string.format("**Calling:** `%s`", tool_name)
  
  if tool_id ~= "" then
    content = content .. string.format(" (ID: `%s`)", tool_id)
  end
  
  local icon = STATUS_ICONS[status] or "⏳"
  content = icon .. " " .. content
  
  -- Add parameters display
  local params = self:_extract_parameters(tool_call)
  if params and params ~= "" then
    content = content .. string.format("\n\n**Parameters:**\n```json\n%s\n```", params)
  end
  
  -- Add to chat buffer with enhanced options
  self.chat:add_buf_message({
    role = config.constants.LLM_ROLE,
    content = content,
  }, {
    type = self.chat.MESSAGE_TYPES.TOOL_MESSAGE,
    tool_call = tool_call,
    status = status,
  })
end

-- Display tool status update
---@param tool_call table The tool call object
---@param status string Current status
function ToolMonitor:_show_status_update(tool_call, status)
  local tool_name = tool_call.name or tool_call.function_call and tool_call.function_call.name or "Unknown Tool"
  local icon = STATUS_ICONS[status] or "⚡"
  
  local content = string.format("%s **Status Update:** `%s` - %s", icon, tool_name, status)
  
  -- Add to chat buffer
  self.chat:add_buf_message({
    role = config.constants.LLM_ROLE,
    content = content,
  }, {
    type = self.chat.MESSAGE_TYPES.TOOL_MESSAGE,
    tool_call = tool_call,
    status = status,
  })
end

-- Display tool output with final status
---@param tool_call table The tool call object
---@param status string Final status
---@param output string? Tool output
---@param duration string Execution duration
function ToolMonitor:_show_tool_output(tool_call, status, output, duration)
  local tool_name = tool_call.name or tool_call.function_call and tool_call.function_call.name or "Unknown Tool"
  local icon = STATUS_ICONS[status] or "✅"
  
  local content = string.format("%s **Result:** `%s`", icon, tool_name)
  
  if duration then
    content = content .. string.format(" (took %s)", duration)
  end
  
  if output and output ~= "" then
    content = content .. string.format("\n\n%s", output)
  else
    content = content .. "\n\n*No output returned*"
  end
  
  -- Add to chat buffer with enhanced options
  self.chat:add_buf_message({
    role = config.constants.LLM_ROLE,
    content = content,
  }, {
    type = self.chat.MESSAGE_TYPES.TOOL_MESSAGE,
    tool_call = tool_call,
    status = status,
    duration = duration,
  })
end

-- Extract and format parameters from tool call
---@param tool_call table The tool call object
---@return string? Formatted parameters
function ToolMonitor:_extract_parameters(tool_call)
  local args = nil
  
  if tool_call.function_call and tool_call.function_call.arguments then
    args = tool_call.function_call.arguments
  elseif tool_call.args then
    args = tool_call.args
  elseif tool_call.parameters then
    args = tool_call.parameters
  end
  
  if not args then
    return nil
  end
  
  -- Format parameters
  if type(args) == "string" then
    -- Try to parse and reformat JSON for better readability
    local ok, parsed = pcall(vim.json.decode, args)
    if ok then
      return vim.json.encode(parsed)
    else
      return args
    end
  elseif type(args) == "table" then
    return vim.json.encode(args)
  else
    return tostring(args)
  end
end

-- Calculate execution duration
---@param tool_id string The tool identifier
---@return string Formatted duration
function ToolMonitor:_calculate_duration(tool_id)
  local start_time = self.start_times[tool_id]
  if not start_time then
    return "unknown"
  end
  
  local end_time = vim.loop.hrtime()
  local duration_ns = end_time - start_time
  local duration_ms = duration_ns / 1000000  -- Convert to milliseconds
  
  if duration_ms < 1000 then
    return string.format("%.0fms", duration_ms)
  else
    return string.format("%.2fs", duration_ms / 1000)
  end
end

-- Clean up any orphaned tools
function ToolMonitor:cleanup()
  for tool_id, tool_info in pairs(self.active_tools) do
    log:warn("Cleaning up orphaned tool: %s", tool_info.name)
    self.active_tools[tool_id] = nil
    self.start_times[tool_id] = nil
  end
end

-- Get current active tools
---@return table List of active tools
function ToolMonitor:get_active_tools()
  local active = {}
  for tool_id, tool_info in pairs(self.active_tools) do
    active[tool_id] = tool_info
  end
  return active
end

M.ToolMonitor = ToolMonitor
return M