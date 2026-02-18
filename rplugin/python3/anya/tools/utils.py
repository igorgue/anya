import threading

from pynvim import Nvim

from agents import RunContextWrapper
from typing import Any


def create_error_handler(_ctx: RunContextWrapper[Any], error: Exception) -> str:
    """Custom error handler for the create tool."""
    return f"Error: {str(error)}"


def nvim_call_sync(nvim: Nvim, func: callable) -> any:
    """Call a function on the main Neovim thread and wait for result."""
    result = [None]
    error = [None]
    event = threading.Event()

    def callback():
        try:
            result[0] = func()
        except Exception as e:
            error[0] = e
        finally:
            event.set()

    nvim.async_call(callback)
    event.wait()

    if error[0]:
        raise error[0]
    return result[0]


def format_tool_header(tool_name: str, first_arg: str, max_len: int = 60) -> str:
    """Format tool header as a minimal 'running title' line.

    Args:
        tool_name: Tool function name
        first_arg: First argument to tool (used as title)
        max_len: Max length before trimming

    Returns:
        running title or running tool_name
    """
    if not first_arg or first_arg == "(no args)":
        return f"running {tool_name}"

    if len(first_arg) > max_len:
        trimmed = first_arg[: max_len - 3] + "..."
    else:
        trimmed = first_arg

    return f"running {trimmed}"
