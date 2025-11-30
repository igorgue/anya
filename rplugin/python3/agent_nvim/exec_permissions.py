"""Exec command permission management for agent.nvim."""

import os
import asyncio
import tempfile
import json
import re
from pathlib import Path

ALLOW_LIST_PATH = Path.home() / ".config" / "agent.nvim" / "exec_allow_list.txt"


def is_yolo_mode() -> bool:
    """Check if YOLO mode is enabled."""
    return os.environ.get("AGENT_YOLO", "").lower() in ("1", "true", "yes")


def load_allow_list() -> set:
    """Load the allow list from disk."""
    if not ALLOW_LIST_PATH.exists():
        return set()

    try:
        with open(ALLOW_LIST_PATH, "r") as f:
            return set(line.strip() for line in f if line.strip())
    except Exception:
        return set()


# Pattern to split on shell command separators: &&, ||, ;, |
COMMAND_SEPARATOR_PATTERN = re.compile(r"\s*(?:&&|\|\||;;|;)\s*")


def get_base_command(command: str) -> str:
    """Extract the base command (first word) for prefix matching.

    e.g., 'ls /home/igor' -> 'ls'
          'git status' -> 'git'
          'python3 script.py' -> 'python3'
    """
    parts = command.split(None, 1)  # Split on first whitespace
    if parts:
        return parts[0]
    return command


def split_chained_commands(command: str) -> list:
    """Split a command string on shell separators (&&, ||, ;, ;;).

    e.g., 'git status && git commit' -> ['git status', 'git commit']
          'ls || echo failed' -> ['ls', 'echo failed']
    """
    return [cmd.strip() for cmd in COMMAND_SEPARATOR_PATTERN.split(command) if cmd.strip()]


def add_to_allow_list(command: str) -> None:
    """Add the base command(s) (first word + space) to the allow list.

    Handles chained commands (&&, ||, ;, ;;) by splitting and adding each.
    e.g., allowing 'git status && ls /home' adds both 'git ' and 'ls '.
    """
    ALLOW_LIST_PATH.parent.mkdir(parents=True, exist_ok=True)

    allow_list = load_allow_list()

    # Split chained commands and add each base command
    for cmd in split_chained_commands(command):
        base_command = get_base_command(cmd)
        allow_list.add(base_command)

    with open(ALLOW_LIST_PATH, "w") as f:
        for cmd in sorted(allow_list):
            f.write(cmd + "\n")


def is_single_command_allowed(command: str, allow_list: set) -> bool:
    """Check if a single command (no chaining) is allowed.

    Matches if the command's base command (first word) is in the allow list.
    e.g., 'ls' in allow list matches 'ls', 'ls /home', 'ls -la', etc.
    """
    base = get_base_command(command)
    return base in allow_list


def is_command_allowed(command: str) -> bool:
    """Check if a command is in the allow list.

    Handles chained commands (&&, ||, ;, ;;) - ALL parts must be allowed.
    e.g., 'ls' in allow list matches 'ls /home', 'ls -la', etc.
    """
    allow_list = load_allow_list()

    # Split chained commands and check each part
    commands = split_chained_commands(command)

    # All commands must be allowed
    for cmd in commands:
        if not is_single_command_allowed(cmd, allow_list):
            return False

    return len(commands) > 0


async def prompt_exec_permission(nvim, command: str, logger=None) -> str:
    """Prompt user for permission to run a command.

    Returns:
        "run" - User chose to run the command
        "deny" - User denied the command
        "always" - User chose to always allow this command
    """
    # Use temp file to communicate result since vim.ui.select is async
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        temp_result = f.name

    # Escape command for JSON encoding
    command_json = json.dumps(command)

    lua_code = f"""
    vim.ui.select(
        {{"Run", "Cancel", "Allow always"}},
        {{
            prompt = "Execute command: " .. {command_json},
            format_item = function(item) return item end
        }},
        function(choice, idx)
            local result = "deny"
            if choice == "Run" then
                result = "run"
            elseif choice == "Allow always" then
                result = "always"
            end
            local file = io.open("{temp_result}", "w")
            if file then
                file:write(vim.fn.json_encode({{choice = result}}))
                file:close()
            end
        end
    )
    """

    def show_select():
        try:
            nvim.exec_lua(lua_code)
        except Exception as e:
            if logger:
                logger.error(f"Error showing exec permission prompt: {e}")
            # Write deny result on error
            try:
                with open(temp_result, "w") as f:
                    json.dump({"choice": "deny"}, f)
            except Exception:
                pass

    nvim.async_call(show_select)

    # Poll for result file to be written (vim.ui.select callback writes it)
    max_wait = 300  # 5 minutes max wait
    poll_interval = 0.1

    for _ in range(int(max_wait / poll_interval)):
        await asyncio.sleep(poll_interval)

        try:
            if os.path.exists(temp_result):
                with open(temp_result, "r") as f:
                    content = f.read().strip()
                    if content:
                        result = json.loads(content)
                        os.unlink(temp_result)
                        return result.get("choice", "deny")
        except Exception:
            pass

    # Cleanup on timeout
    try:
        os.unlink(temp_result)
    except Exception:
        pass

    return "deny"
