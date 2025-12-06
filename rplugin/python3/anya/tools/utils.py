import threading

from pynvim import Nvim


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
