import os
import shutil
import subprocess
from agents import function_tool, RunContextWrapper

from ..agents.context import NvimPluginContext
from .utils import create_error_handler


@function_tool(failure_error_function=create_error_handler)
async def list_files(
    ctx: RunContextWrapper[NvimPluginContext],
    path: str = ".",
    max_results: int = 100,
) -> str:
    """Lists files in a directory (recursive, respects gitignore if possible).

    Args:
        path: Directory path to list (default current directory)
        max_results: Maximum number of files to return (default 100)

    Returns:
        Newline-separated list of file paths, or error message
    """
    # Get cwd from context (from user's Neovim)
    plugin_context = ctx.context
    cwd = plugin_context.cwd if plugin_context.cwd else os.getcwd()

    # Expand ~ to home directory and environment variables
    path = os.path.expandvars(os.path.expanduser(path))

    if not os.path.isabs(path):
        target_dir = os.path.join(cwd, path)
    else:
        target_dir = path

    # Check if directory exists
    if not os.path.exists(target_dir):
        raise Exception(f"Directory does not exist: {path}")

    if not os.path.isdir(target_dir):
        raise Exception(f"Path is not a directory: {path}")

    # Find fd executable
    fd_path = shutil.which("fd")
    if not fd_path:
        raise Exception(
            "fd not found. Please install fd: https://github.com/sharkdp/fd"
        )

    # Use fd to list files (respects .gitignore by default)
    result = subprocess.run(
        [fd_path, "--type", "f", ".", target_dir],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        lines = result.stdout.strip().split("\n")
        if len(lines) > max_results:
            output = (
                "\n".join(lines[:max_results])
                + f"\n... (truncated, showing {max_results} of {len(lines)} files)"
            )
        else:
            output = result.stdout.strip()
        return f"\n{output}\n"
    else:
        raise Exception(f"fd command failed: {result.stderr}")
