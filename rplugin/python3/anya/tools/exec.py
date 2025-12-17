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
    timeout: int = 30,
) -> str:
    """Execute a shell command and return stdout and stderr.

    SAFETY: This tool requires user confirmation before executing commands.
    Commands can be allowed for the current session.

    Args:
        command: Shell command to execute
        timeout: Timeout in seconds (default 30)

    Returns:
        Combined output with stdout and stderr, or error message
    """
    plugin_context = ctx.context

    # Extract just the command name
    cmd_name = _extract_command_name(command)

    # Check YOLO mode from context
    yolo_mode = plugin_context.yolo_mode

    # Check if this command is already allowed in this session
    if cmd_name in plugin_context.allowed_commands:
        # Execute without asking
        pass
    elif yolo_mode:
        # YOLO mode: auto-allow and execute without asking
        plugin_context.allowed_commands.add(cmd_name)
    else:
        # Request user confirmation
        choice = None

        if plugin_context.has_nvim:
            # Direct Neovim access - use UI select
            nvim = plugin_context.nvim
            choice = await _nvim_ui_select(
                nvim,
                ["Execute", "Allow for this session", "Cancel"],
                f"Execute command?\n\n{command[:100]}",
            )
        elif plugin_context.confirmation_callback:
            # Daemon mode with confirmation callback
            choice = await plugin_context.confirmation_callback(
                f"Execute command?\n\n{command[:100]}",
                ["Execute", "Allow for this session", "Cancel"],
            )
        else:
            # No confirmation mechanism available - require YOLO mode
            raise Exception(
                f"Command '{cmd_name}' requires user confirmation. "
                "Run in YOLO mode (set g:anya_yolo_mode=1) to auto-approve commands, "
                "or use direct Neovim mode."
            )

        if choice == "Allow for this session":
            # Add to allowed commands and execute
            plugin_context.allowed_commands.add(cmd_name)
        elif choice and choice != "Execute":
            raise Exception("Command execution cancelled by user")
        elif not choice:
            raise Exception("No response received from user confirmation")

    # Get cwd from context (from user's Neovim)
    cwd = plugin_context.cwd if plugin_context.cwd else os.getcwd()

    # Daemon mode with exec callback - delegate to plugin for execution on user's machine
    if plugin_context.exec_callback:
        result = await plugin_context.exec_callback(command, cwd, timeout)

        if result.get("error"):
            raise Exception(result["error"])

        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        returncode = result.get("returncode", 0)

        return _format_exec_output(stdout, stderr, returncode)

    # Direct execution (local or direct Neovim mode)
    return _execute_command_locally(command, cwd, timeout)


def _execute_command_locally(command: str, cwd: str, timeout: int) -> str:
    """Execute a command locally using subprocess."""
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

    return _format_exec_output(stdout, stderr, process.returncode)


def _format_exec_output(stdout: str, stderr: str, returncode: int) -> str:
    """Format exec output with stdout, stderr, and exit code."""
    output_parts = []

    if returncode == 0:
        # Command succeeded
        if stdout:
            output_parts.append(stdout.rstrip())
        if not output_parts:
            output_parts.append("(command completed successfully)")
        # Don't show stderr for successful commands - it's usually just warnings
    else:
        # Command failed - show everything
        if stdout:
            output_parts.append(f"STDOUT:\n{stdout}")
        if stderr:
            if output_parts:
                output_parts.append("")
            output_parts.append(f"STDERR:\n{stderr}")
        output_parts.append(f"\nExit code: {returncode}")

    result = "\n".join(output_parts) if output_parts else "(no output)"
    return f"{result}\n"
