# Enhanced Tool Call Display Configuration

To enable enhanced tool call and output display in CodeCompanion, add the following to your configuration:

## Method 1: Use the Enhanced Tools Formatter

```lua
{
  "olimorris/codecompanion.nvim",
  dependencies = {
    "nvim-lua/plenary.nvim",
    "nvim-treesitter/nvim-treesitter",
  },
  config = function()
    require("codecompanion").setup({
      strategies = {
        chat = {
          tools = {
            -- Enable enhanced tool display
            opts = {
              folds = {
                enabled = true,  -- Enable folding for long outputs
              },
            },
          },
        },
      },
      -- Enhanced display configuration
      display = {
        chat = {
          icons = {
            -- Enhanced tool icons
            tool_pending = "⏳",
            tool_in_progress = "⚡", 
            tool_success = "✅",
            tool_failure = "❌",
            -- Additional icons for enhanced display
            tool_call = "🔧",
            tool_params = "📋",
            tool_output = "📤",
            tool_duration = "⏱️",
          },
        },
      },
    })

    -- Register the enhanced tools formatter
    local EnhancedTools = require("codecompanion.strategies.chat.ui.formatters.enhanced_tools")
    local chat = require("codecompanion.strategies.chat")
    
    -- Override the tools formatter
    chat.Chat.UI.Builder._formatters = vim.tbl_extend("force", 
      chat.Chat.UI.Builder._formatters or {},
      { EnhancedTools }
    )
  end,
}
```

## Method 2: Enable Tool Monitoring

```lua
-- In your configuration or after setup:
local ToolMonitor = require("codecompanion.strategies.chat.ui.tool_monitor")

-- Hook into tool execution to show enhanced display
local original_add_tool_output = require("codecompanion.strategies.chat").Chat.add_tool_output

require("codecompanion.strategies.chat").Chat.add_tool_output = function(self, tool, for_llm, for_user)
  -- Create tool monitor if not exists
  if not self._tool_monitor then
    self._tool_monitor = ToolMonitor.new(self)
  end
  
  local tool_call = tool.function_call or tool
  local tool_id = tool_call.id
  
  if tool_id and not self._tool_monitor.active_tools[tool_id] then
    -- Start monitoring the tool
    self._tool_monitor:start_tool(tool_call)
  end
  
  -- Call original function
  original_add_tool_output(self, tool, for_llm, for_user)
  
  -- Update status
  if tool_id then
    self._tool_monitor:update_status(tool_id, "completed", for_user or for_llm)
  end
end
```

## Method 3: Custom Integration

For a more integrated approach, you can modify the tool orchestrator:

```lua
-- Hook into the tool orchestrator to show tool calls immediately
local orchestrator = require("codecompanion.strategies.chat.tools.orchestrator")

-- Wrap the tool execution to show calls immediately
local original_cmd_to_func_tool = orchestrator.cmd_to_func_tool

orchestrator.cmd_to_func_tool = function(tool)
  local enhanced_tool = original_cmd_to_func_tool(tool)
  
  -- Wrap the tool execution
  local original_cmds = enhanced_tool.cmds
  enhanced_tool.cmds = vim.tbl_map(function(cmd)
    if type(cmd) == "function" then
      return function(tools, input, callback)
        -- Show tool call before execution
        local chat = tools.chat
        if chat._tool_monitor then
          chat._tool_monitor:start_tool(enhanced_tool)
        end
        
        -- Execute the original command
        cmd(tools, input, function(result)
          -- Update status after completion
          if chat._tool_monitor and enhanced_tool.id then
            local status = (result.status == "success") and "completed" or "failed"
            chat._tool_monitor:update_status(enhanced_tool.id, status, result.data)
          end
          
          callback(result)
        end)
      end
    end
    return cmd
  end, original_cmds)
  
  return enhanced_tool
end
```

## Features Enabled

With these configurations, you'll get:

1. **Tool Call Display**: Shows tool name, ID, and parameters before execution
2. **Status Updates**: Real-time status icons (⏳ pending, ⚡ in_progress, ✅ completed, ❌ failed)
3. **Enhanced Formatting**: Better visual separation between tool calls and outputs
4. **Duration Tracking**: Shows how long each tool call took to execute
5. **Parameter Display**: Shows tool parameters in formatted JSON code blocks
6. **Error Handling**: Better display of tool failures and error messages
7. **Folding**: Long outputs can be folded for better readability

## Customization

You can customize the display by:

1. **Changing Icons**: Modify the icons in the display configuration
2. **Adjusting Formatting**: Edit the enhanced_tools.lua file to change the display format
3. **Adding Timing Information**: Modify the tool_monitor.lua to track more detailed timing
4. **Custom Status Messages**: Add custom status messages for different tool types

## Example Output

With enhanced display enabled, tool calls will appear as:

```
⏳ Calling: `cmd_runner` (ID: `call_abc123`)

📋 Parameters:
```json
{
  "cmd": "ls -la",
  "flag": null
}
```
---
⚡ Status Update: `cmd_runner` - in_progress
✅ Result: `cmd_runner` (took 245ms)

total 48
drwxr-xr-x  5 user user 4096 Dec 10 12:00 .
drwxr-xr-x 12 user user 4096 Dec 10 11:30 ..
...
```