import threading

from pynvim import Nvim

from agents import RunContextWrapper
from typing import Any


def create_error_handler(ctx: RunContextWrapper[Any], error: Exception) -> str:
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
