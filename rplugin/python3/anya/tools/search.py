import os
import subprocess
from agents import function_tool

from .utils import create_error_handler


@function_tool(failure_error_function=create_error_handler)
async def search(query: str, cwd: str = None, max_results: int = 2000) -> str:
    """Searches the project files for a string using grep/ripgrep.

    **IMPORTANT**: This tool is for files only, it's not a generic web search tool.

    Args:
        query: Search query string
        cwd: Current working directory to search in
        max_results: Maximum number of results to return

    Returns:
        Search results with line numbers, or error message
    """
    if cwd is None:
        # Use os.getcwd() which will be the daemon's CWD (updated per-request from client)
        cwd = os.getcwd()

    # Expand ~ to home directory and environment variables
    cwd = os.path.expandvars(os.path.expanduser(cwd))

    # Try ripgrep first
    cmd = ["rg", "--line-number", "--no-heading", "--smart-case", query, cwd]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            output = result.stdout[:2000]  # Limit output
            return f"n{output}\n"
    except FileNotFoundError:
        # Fallback to grep
        cmd = ["grep", "-rn", query, cwd]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                output = result.stdout[:max_results]
                return f"n{output}\n"
        except FileNotFoundError:
            raise Exception(
                "Neither ripgrep (rg) nor grep found. Please install one of them."
            )

    return "No matches found."
