#!/usr/bin/env lua

--[[
Demonstration script for enhanced tool call display
Run this in Neovim with :luafile demo_enhanced_tools.lua
--]]

-- Load required modules
local chat = require("codecompanion.strategies.chat")

-- Demo function to show enhanced tool display
local function demo_enhanced_tools()
  print("=== Enhanced Tool Call Display Demo ===\n")
  
  -- Example 1: Show what a tool call looks like
  print("Example 1: Tool Call Display")
  print("⏳ Calling: `read_file` (ID: `call_RJU6xfk0OzQF3Gg9cOFS5RY7`)")
  print()
  print("📋 Parameters:")
  print("```json")
  print('{')
  print('  "filepath": "/home/user/project/README.md",')
  print('  "start_line_number_base_zero": 0,')
  print('  "end_line_number_base_zero": 50')
  print('}')
  print("```")
  print("---")
  print("⚡ Status Update: `read_file` - in_progress")
  print("✅ Result: `read_file` (took 15ms)")
  print()
  print("# README")
  print()
  print("This is the content of the README file...")
  print()
  
  -- Example 2: Tool with error
  print("Example 2: Tool with Error")
  print("⏳ Calling: `cmd_runner` (ID: `call_XYZ789`)")
  print()
  print("📋 Parameters:")
  print("```json")
  print('{')
  print('  "cmd": "nonexistent_command",')
  print('  "flag": null')
  print('}')
  print("```")
  print("---")
  print("❌ Result: `cmd_runner` (took 890ms)")
  print()
  print("Error: Command 'nonexistent_command' not found")
  print()
  
  -- Example 3: Tool with long output (folding)
  print("Example 3: Tool with Long Output")
  print("⏳ Calling: `list_files` (ID: `call_DEF456`)")
  print()
  print("📋 Parameters:")
  print("```json")
  print('{')
  print('  "directory": "/home/user/project",')
  print('  "recursive": true')
  print('}')
  print("```")
  print("---")
  print("✅ Result: `list_files` (took 1.23s)")
  print()
  print("📂 Found 156 files:")
  print("```")
  print("src/")
  print("├── main.js")
  print("├── utils.js")
  print("├── components/")
  print("│   ├── Button.jsx")
  print("│   ├── Modal.jsx")
  print("│   └── index.js")
  print("├── services/")
  print("│   ├── api.js")
  print("│   ├── auth.js")
  print("│   └── database.js")
  print("├── tests/")
  print("│   ├── unit/")
  print("│   ├── integration/")
  print("│   └── e2e/")
  print("└── docs/")
  print("    ├── API.md")
  print("    ├── SETUP.md")
  print("    └── CONTRIBUTING.md")
  print("```")
  print()
  print("📁 42 directories, 156 files total")
  
  print("\n=== Demo Complete ===")
end

-- Show usage instructions
print("Enhanced Tool Call Display Demo")
print("This demo shows how tool calls and outputs will be displayed with enhanced formatting.")
print()
print("To enable this in your CodeCompanion setup:")
print("1. Add the enhanced_tools.lua formatter to your configuration")
print("2. Enable the tool_monitor.lua for real-time updates")
print("3. Configure icons and display options as needed")
print()
print("Press Enter to see the demo output...")
io.read()

-- Run the demo
demo_enhanced_tools()