"""Modify the current Neovim buffer content.

Uses a temp-file rendezvous pattern (similar to ui.py) that the plugin
monitors while the subprocess is running. Works in both daemon mode
(during :Anya do) and direct Neovim mode.

Usage:
    from anya.libs import buffer

    # Replace buffer contents
    buffer.modify("def hello():\n    print('hello')")

    # Append to buffer
    buffer.modify("# Added line", mode="append")

    # Prepend to buffer
    buffer.modify("# Header", mode="prepend")

Note: This lib only works inside run_code() calls when a current buffer
context is available (e.g., during :Anya do operations).
"""

import json
import os
import time
import uuid


def _ui_dir() -> str | None:
    """Return the UI rendezvous directory injected by run_code, or None."""
    return os.environ.get("ANYA_UI_DIR")


def modify(content: str, mode: str = "replace", timeout: float = 30.0) -> str:
    """Modify the current Neovim buffer content.

    This writes content directly to the current buffer in Neovim.
    Use this when the agent wants to modify the file the user is currently editing.

    Args:
        content: The content to write to the buffer.
        mode: How to modify the buffer:
            - "replace": Replace entire buffer content (default)
            - "append": Append to the end of the buffer
            - "prepend": Insert at the beginning of the buffer
        timeout: Seconds to wait before raising TimeoutError (default 30).

    Returns:
        Success message or error description.

    Raises:
        RuntimeError: If not running inside a run_code call (no ANYA_UI_DIR).
        TimeoutError: If the plugin doesn't respond within *timeout* seconds.
        ValueError: If mode is invalid.

    Example:
        from anya.libs import buffer

        # Replace buffer with new code
        result = buffer.modify("def new_function():\n    pass")
        print(result)

        # Append a line
        result = buffer.modify("\n# End of file", mode="append")
    """
    ui_dir = _ui_dir()
    if not ui_dir:
        raise RuntimeError(
            "anya.libs.buffer is only available inside run_code() calls. "
            "ANYA_UI_DIR is not set."
        )

    if mode not in ("replace", "append", "prepend"):
        raise ValueError(
            f"Invalid mode: {mode!r}. Use 'replace', 'append', or 'prepend'."
        )

    request_id = str(uuid.uuid4())
    request_file = os.path.join(ui_dir, f"{request_id}.request.json")
    response_file = os.path.join(ui_dir, f"{request_id}.response.json")

    request_data = {
        "id": request_id,
        "kind": "modify_buffer",
        "content": content,
        "mode": mode,
    }
    with open(request_file, "w") as f:
        json.dump(request_data, f)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(response_file):
            with open(response_file) as f:
                response = json.load(f)
            try:
                os.unlink(response_file)
            except OSError:
                pass
            return response.get("result", "")
        time.sleep(0.05)

    # Clean up stale request on timeout
    try:
        os.unlink(request_file)
    except OSError:
        pass
    raise TimeoutError(f"Buffer modify request timed out after {timeout}s")
