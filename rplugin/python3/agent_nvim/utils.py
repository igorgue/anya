"""Utility functions for agent.nvim plugin."""

import os
import re
import json
from datetime import datetime


def resolve_mentions(text: str, read_file_fn) -> str:
    """Replaces @file mentions with file content, supporting line range syntax.

    Syntax:
        @filename.py              - Read first 100 lines (default)
        @filename.py@start-end    - Read entire file
        @filename.py@32-234       - Read lines 32-234
        @filename.py@start-100    - Read lines 1-100
        @filename.py@3202-end     - Read from line 3202 to end

    Args:
        text: Text containing @file mentions (with optional @start-end ranges)
        read_file_fn: Function to read file content, signature: fn(path_with_range) -> str

    Returns:
        Text with @mentions replaced with file contents
    """

    def replace_match(match):
        path_spec = match.group(1)
        content = read_file_fn(path_spec)
        if content.startswith("Error"):
            return f"[Error reading {path_spec}: {content}]"
        return f"\n{content}\n"

    # Match @path/to/file or @filename, optionally followed by @range
    # Pattern: @ followed by path characters, optionally @start-end
    # Allows: @file.py, @path/to/file.py, @file.py@32-100, @file.py@start-end, etc.
    return re.sub(r"@([\w./-]+(?:@[\w-]+)?)", replace_match, text)


def load_project_instructions(cwd: str) -> str:
    """Loads project-specific instructions from AGENTS.md or .agent/instructions.md.

    Args:
        cwd: Current working directory to search in

    Returns:
        Project instructions as string, or empty string if not found
    """
    candidates = ["AGENTS.md", ".agent/instructions.md"]

    for cand in candidates:
        path = os.path.join(cwd, cand)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                pass
    return ""


def get_current_timestamp() -> str:
    """Get current timestamp as a formatted string.

    Returns:
        Current timestamp in ISO format
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def emit_user_event(nvim, event_name: str, data: dict):
    """Emit a User autocommand event with data for fidget integration.

    Args:
        nvim: Neovim instance
        event_name: Name of the event to emit
        data: Dictionary of data to pass with the event
    """
    try:
        # Serialize data to JSON
        data_json = json.dumps(data)
        # Use Lua bracket notation [[...]] to avoid quote escaping issues
        lua_code = f"""vim.api.nvim_exec_autocmds('User', {{pattern = '{event_name}', data = vim.fn.json_decode([[{data_json}]])}})"""
        # Execute doautocmd with data
        nvim.async_call(lambda: nvim.exec_lua(lua_code))
    except Exception as e:
        # Can't use logger here as it's not passed, will be logged at call site if needed
        pass
