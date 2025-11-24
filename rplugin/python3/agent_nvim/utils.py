"""Utility functions for agent.nvim plugin."""

import os
import re
import json


def resolve_mentions(text: str, read_file_fn) -> str:
    """Replaces @file mentions with file content.
    
    Args:
        text: Text containing @file mentions
        read_file_fn: Function to read file content, signature: fn(path) -> str
        
    Returns:
        Text with @mentions replaced with file contents
    """
    def replace_match(match):
        path = match.group(1)
        content = read_file_fn(path)
        if content.startswith("Error"):
            return f"[Error reading {path}: {content}]"
        return f"\n--- Start of {path} ---\n{content}\n--- End of {path} ---\n"

    # Match @path/to/file or @filename
    # Simple regex: @ followed by non-whitespace characters
    return re.sub(r"@([\w./-]+)", replace_match, text)


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
        # Escape single quotes for Vim command
        data_json_escaped = data_json.replace("'", "''")
        # Execute doautocmd with data
        nvim.async_call(
            lambda: nvim.exec_lua(
                f"vim.api.nvim_exec_autocmds('User', {{pattern = '{event_name}', data = vim.fn.json_decode('{data_json_escaped}')}})"
            )
        )
    except Exception as e:
        # Can't use logger here as it's not passed, will be logged at call site if needed
        pass
