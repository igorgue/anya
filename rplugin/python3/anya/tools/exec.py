import asyncio
import os
import subprocess
import shlex
from agents import function_tool, RunContextWrapper

from ..agents.context import NvimPluginContext
from .utils import create_error_handler


def _extract_command_name(command: str) -> str:
    """Extract the command name from a full shell command.

    Examples:
        'ls -la' -> 'ls'
        'grep -r pattern' -> 'grep'
        '/usr/bin/python script.py' -> 'python'
    """
    try:
        # Use shlex to parse the command properly
        parts = shlex.split(command)
        if not parts:
            return command

        # Get the first part
        cmd = parts[0]

        # If it's a path, get just the basename
        if "/" in cmd:
            return os.path.basename(cmd)

        return cmd
    except ValueError:
        # If shlex fails, fallback to simple space split
        cmd = command.split()[0] if command.split() else command
        if "/" in cmd:
            return os.path.basename(cmd)
        return cmd


async def _nvim_ui_select(nvim, options: list, prompt: str) -> str:
    """Ask user to select from options using vim.ui.select."""
    # Format options for Lua table
    lua_options = "{" + ", ".join(f'"{opt}"' for opt in options) + "}"
    lua_prompt = prompt.replace('"', '\\"').replace("\n", "\\n")

    def run_select():
        nvim.exec_lua(
            f"""
vim.g.anya_select_result = nil
vim.ui.select({lua_options},
    {{prompt = "{lua_prompt}"}},
    function(selection)
        vim.g.anya_select_result = selection or "Cancel"
    end)
"""
        )

    # Wrap the Neovim calls with async_call
    nvim.async_call(run_select)

    # Poll for the result with async sleep
    start_time = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start_time < 30.0:
        result = [None]

        def get_result():
            try:
                result[0] = nvim.eval("get(g:, 'anya_select_result', v:null)")
            except Exception:
                pass

        nvim.async_call(get_result)
        await asyncio.sleep(0.05)

        if result[0] is not None:
            return result[0]

    return "Cancel"


@function_tool(failure_error_function=create_error_handler)
async def exec(
    ctx: RunContextWrapper[NvimPluginContext],
    command: str,
    cwd: str = None,
    timeout: int = 30,
) -> str:
    """Execute a shell command and return stdout and stderr.

    SAFETY: This tool requires user confirmation before executing commands.
    Commands can be allowed for the current session.

    Args:
        command: Shell command to execute
        cwd: Current working directory for the command (defaults to current directory)
        timeout: Timeout in seconds (default 30)

    Returns:
        Combined output with stdout and stderr, or error message
    """
    nvim = ctx.context.nvim
    plugin_context = ctx.context

    # Extract just the command name
    cmd_name = _extract_command_name(command)

    # Check if this command is already allowed in this session
    if cmd_name in plugin_context.allowed_commands:
        # Execute without asking
        pass
    else:
        # Ask user for confirmation using vim.ui.select
        choice = await _nvim_ui_select(
            nvim,
            ["Execute", "Allow for this session", "Cancel"],
            f"Execute command?\n\n{command[:100]}",
        )

        if choice == "Allow for this session":
            # Add to allowed commands and execute
            plugin_context.allowed_commands.add(cmd_name)
        elif choice != "Execute":
            raise Exception("Command execution cancelled by user")

    if cwd is None:
        cwd = os.getcwd()
    else:
        cwd = os.path.expandvars(os.path.expanduser(cwd))

    # Use Popen to get full control over stdout/stderr
    process = subprocess.Popen(
        command,
        shell=True,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        raise Exception(f"Command timed out after {timeout} seconds")

    # Build output with both stdout and stderr
    output_parts = []

    if stdout:
        output_parts.append(f"STDOUT:\n{stdout}")

    if stderr:
        if output_parts:
            output_parts.append("")  # Add blank line separator
        output_parts.append(f"STDERR:\n{stderr}")

    if process.returncode != 0:
        if output_parts:
            output_parts.append("")
        output_parts.append(f"Exit code: {process.returncode}")

    result = "\n".join(output_parts) if output_parts else "(no output)"
    return f"n{result}\n"
